from datetime import date, timedelta
import re
from urllib.parse import urlparse

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.test import override_settings
from django.utils import timezone
from unittest.mock import patch
from django.db import IntegrityError
from sgr_app.models import (
    Delegacion, Cargo, Usuario, PeriodoMedicion, ItemMedicion,
    Actividad, Evidencia, CompromisoAgenda, CasoSocial, AtencionSocial,
    MetaModel, RegistroAuditoria, generar_codigo_evidencia
)
from sgr_app.services.calculos import (
    calcular_dias_periodo,
    determinar_color_semaforo,
    calcular_metricas_funcionario,
    calcular_resumen_delegacion,
    calcular_resumen_comunal
)

class MatrizSGRTestSuite(TestCase):
    def setUp(self):
        self.delegacion = Delegacion.objects.create(nombre="Las Compañías", codigo="CIA")
        self.cargo = Cargo.objects.create(nombre="Gestor Territorial")
        self.usuario = Usuario.objects.create_user(
            username="carlos.test",
            password="Password123!",
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=self.cargo,
            delegacion=self.delegacion,
            first_name="Carlos",
            last_name="Tapia"
        )
        self.verificador = Usuario.objects.create_user(
            username="pablo.cuadra",
            password="Password123!",
            rol=Usuario.Rol.SUPERVISOR,
            es_delegado=True,
            cargo=self.cargo,
            delegacion=self.delegacion,
            first_name="Pablo",
            last_name="Cuadra"
        )
        self.periodo = PeriodoMedicion.objects.create(
            nombre="T3 Test 2026",
            fecha_inicio=date(2026, 7, 1),
            fecha_termino=date(2026, 9, 30),
            tope_cumplimiento=150.0
        )
        # Ítems que suman 100% de ponderación (RN-001)
        self.item1 = ItemMedicion.objects.create(
            cargo=self.cargo, periodo=self.periodo,
            nombre_item="Operativos Terreno", meta_cuantitativa=10.0, ponderador=60.0
        )
        self.item2 = ItemMedicion.objects.create(
            cargo=self.cargo, periodo=self.periodo,
            nombre_item="Atención Solicitudes", meta_cuantitativa=5.0, ponderador=40.0
        )

    def test_user_roles_are_limited_and_delegate_is_a_supervisor_profile(self):
        self.assertEqual(
            set(Usuario.Rol.values),
            {'ADMINISTRADOR', 'SUPERVISOR', 'ADMINISTRATIVO'},
        )
        self.assertEqual(self.verificador.rol, Usuario.Rol.SUPERVISOR)
        self.assertTrue(self.verificador.es_delegado)
        self.assertFalse(self.verificador.es_supervisor)
        self.assertTrue(self.verificador.es_coordinador_o_delegado())

    # 1. Reglas de Cálculo del Semáforo y Días Computables (RN-007, RN-008)
    def test_dias_computables_y_meta_esperada(self):
        fecha_mitad = date(2026, 8, 15)
        res = calcular_dias_periodo(self.periodo, fecha_mitad)
        self.assertEqual(res['dias_totales'], 92)
        self.assertTrue(0 < res['meta_esperada_pct'] < 100)

    def test_clasificacion_semaforo(self):
        # Verde si avance >= meta esperada
        self.assertEqual(determinar_color_semaforo(50.0, 50.0), 'VERDE')
        self.assertEqual(determinar_color_semaforo(60.0, 50.0), 'VERDE')
        # Ámbar si avance >= 60% de lo esperado y < esperado
        self.assertEqual(determinar_color_semaforo(35.0, 50.0), 'AMBAR')
        self.assertEqual(determinar_color_semaforo(30.0, 50.0), 'AMBAR')
        # Rojo si avance < 60% de lo esperado
        self.assertEqual(determinar_color_semaforo(25.0, 50.0), 'ROJO')

    def test_clasificacion_semaforo_uses_period_thresholds(self):
        self.assertEqual(
            determinar_color_semaforo(55.0, 50.0, umbral_verde=110.0, umbral_ambar=50.0),
            'VERDE',
        )
        self.assertEqual(
            determinar_color_semaforo(52.0, 50.0, umbral_verde=110.0, umbral_ambar=50.0),
            'AMBAR',
        )
        self.assertEqual(
            determinar_color_semaforo(24.0, 50.0, umbral_verde=110.0, umbral_ambar=50.0),
            'ROJO',
        )

    def test_periodic_amber_target_uses_configured_threshold(self):
        self.periodo.umbral_semaforo_ambar = 40.0

        info = calcular_dias_periodo(self.periodo, date(2026, 8, 15))

        self.assertEqual(info['umbral_ambar_ratio'], 40.0)
        self.assertEqual(
            info['umbral_60_dia'],
            round(info['meta_esperada_pct'] * 0.40, 2),
        )

    # 2. Criterio CA-01 y CA-02: Validación de Evidencia Aprobada vs Rechazada
    def test_ca01_ca02_validacion_evidencia(self):
        act = Actividad.objects.create(
            funcionario=self.usuario, delegacion=self.delegacion,
            item_medicion=self.item1, actividad_solicitud="Bacheo pasaje",
            accion_ejecutada="Reparado", fecha_actividad=date(2026, 8, 1)
        )
        ev = Evidencia.objects.create(actividad=act, estado=Evidencia.Estado.PENDIENTE)

        # Pendiente: avance es 0
        m1 = calcular_metricas_funcionario(self.usuario, self.periodo, date(2026, 8, 15))
        item_data = next(i for i in m1['items'] if i['item_id'] == self.item1.id)
        self.assertEqual(item_data['avance_aprobado'], 0)

        # CA-01: Aprobar evidencia aumenta avance a 1
        ev.estado = Evidencia.Estado.APROBADA
        ev.verificador = self.verificador
        ev.save()

        m2 = calcular_metricas_funcionario(self.usuario, self.periodo, date(2026, 8, 15))
        item_data2 = next(i for i in m2['items'] if i['item_id'] == self.item1.id)
        self.assertEqual(item_data2['avance_aprobado'], 1)
        self.assertEqual(item_data2['cumplimiento_pct'], 10.0)

        # CA-02: Rechazar evidencia con observación no suma y conserva motivo
        ev.estado = Evidencia.Estado.RECHAZADA
        ev.observacion = "Foto no nítida"
        ev.save()

        m3 = calcular_metricas_funcionario(self.usuario, self.periodo, date(2026, 8, 15))
        item_data3 = next(i for i in m3['items'] if i['item_id'] == self.item1.id)
        self.assertEqual(item_data3['avance_aprobado'], 0)
        self.assertEqual(ev.observacion, "Foto no nítida")

    def test_metricas_solo_cuentan_evidencia_aprobada_dentro_del_periodo(self):
        actividades = (
            (date(2026, 6, 30), Evidencia.Estado.APROBADA),
            (date(2026, 7, 1), Evidencia.Estado.APROBADA),
            (date(2026, 9, 30), Evidencia.Estado.APROBADA),
            (date(2026, 10, 1), Evidencia.Estado.APROBADA),
            (date(2026, 8, 15), Evidencia.Estado.PENDIENTE),
        )
        for fecha, estado in actividades:
            actividad = Actividad.objects.create(
                funcionario=self.usuario,
                delegacion=self.delegacion,
                item_medicion=self.item1,
                fecha_actividad=fecha,
            )
            Evidencia.objects.create(actividad=actividad, estado=estado)
            if fecha == date(2026, 7, 1):
                Evidencia.objects.create(actividad=actividad, estado=estado)

        metricas = calcular_metricas_funcionario(
            self.usuario,
            self.periodo,
            date(2026, 8, 15),
        )
        item = next(i for i in metricas['items'] if i['item_id'] == self.item1.id)

        self.assertEqual(item['avance_aprobado'], 2)
        self.assertEqual(item['cumplimiento_pct'], 20.0)

    def test_ponderadores_no_superan_el_cien_por_cargo_y_periodo(self):
        with self.assertRaises(ValidationError):
            ItemMedicion.objects.create(
                cargo=self.cargo,
                periodo=self.periodo,
                nombre_item='Ítem con exceso de ponderación',
                meta_cuantitativa=1,
                ponderador=1,
            )

        MetaModel.objects.create(
            nombre='Meta ponderada 60',
            valor_meta=10,
            ponderador=60,
            periodo=self.periodo,
        )
        MetaModel.objects.create(
            nombre='Meta ponderada 40',
            valor_meta=10,
            ponderador=40,
            periodo=self.periodo,
        )
        with self.assertRaises(ValidationError):
            MetaModel.objects.create(
                nombre='Meta ponderada excedida',
                valor_meta=10,
                ponderador=1,
                periodo=self.periodo,
            )

    # 3. Criterio CA-03: Compromisos Vencidos en Tubo de Trabajo
    def test_ca03_compromiso_vencido(self):
        hoy = timezone.now().date()
        ayer = hoy - timedelta(days=1)
        manana = hoy + timedelta(days=2)

        comp_vencido = CompromisoAgenda.objects.create(
            solicitante="Vecino A", delegacion=self.delegacion,
            responsable=self.usuario, fecha_comprometida=ayer,
            descripcion="Plaza", estado=CompromisoAgenda.Estado.EN_PROCESO
        )
        comp_al_dia = CompromisoAgenda.objects.create(
            solicitante="Vecino B", delegacion=self.delegacion,
            responsable=self.usuario, fecha_comprometida=manana,
            descripcion="Luminaria", estado=CompromisoAgenda.Estado.EN_PROCESO
        )

        resumen = calcular_resumen_delegacion(self.delegacion, self.periodo)
        self.assertEqual(resumen['compromisos']['vencidos'], 1)
        self.assertEqual(resumen['compromisos']['total'], 2)

    # 4. Regla RN-005: Cumplimiento Ponderado con Tope Máximo 150%
    def test_rn005_tope_maximo_cumplimiento(self):
        # Meta es 10 actividades. Registramos 20 actividades aprobadas (200%)
        for i in range(20):
            act = Actividad.objects.create(
                funcionario=self.usuario, delegacion=self.delegacion,
                item_medicion=self.item1, actividad_solicitud=f"Actividad {i}",
                accion_ejecutada="Ejecutada", fecha_actividad=date(2026, 8, 1)
            )
            Evidencia.objects.create(actividad=act, estado=Evidencia.Estado.APROBADA)

        metricas = calcular_metricas_funcionario(self.usuario, self.periodo, date(2026, 8, 15))
        it = next(i for i in metricas['items'] if i['item_id'] == self.item1.id)

        self.assertEqual(it['cumplimiento_pct'], 200.0)
        # Con tope de 150%, aporte ponderado = (60% * 150) / 100 = 90.0%
        self.assertEqual(it['aporte_ponderado'], 90.0)

    # 5. Código Único e Inmutable de Evidencia (RN-010, HU-10)
    def test_generador_codigo_evidencia(self):
        cod = generar_codigo_evidencia("CIA")
        self.assertTrue(cod.startswith("CIA"))
        self.assertEqual(len(cod), 9)

    def test_evidence_verification_code_retries_collisions_and_is_immutable(self):
        actividad = Actividad.objects.create(
            funcionario=self.usuario,
            delegacion=self.delegacion,
            item_medicion=self.item1,
            fecha_actividad=date(2026, 8, 1),
        )
        primera = Evidencia.objects.create(actividad=actividad)
        codigo_original = primera.codigo_verificador
        codigo_alternativo = f'{self.delegacion.codigo}XYZ123'

        with patch(
            'sgr_app.models.generar_codigo_evidencia',
            side_effect=(codigo_original, codigo_alternativo),
        ):
            segunda = Evidencia.objects.create(actividad=actividad)

        self.assertEqual(segunda.codigo_verificador, codigo_alternativo)
        self.assertNotEqual(primera.codigo_verificador, segunda.codigo_verificador)

        primera.codigo_verificador = codigo_alternativo
        with self.assertRaises(ValidationError):
            primera.save()

        primera.refresh_from_db()
        self.assertEqual(primera.codigo_verificador, codigo_original)
        self.assertFalse(Evidencia.objects.filter(
            codigo_verificador=codigo_alternativo,
        ).exclude(pk=segunda.pk).exists())

    # 6. Casos Sociales con Secuencia de 3 Etapas (RF-015, CA-04)
    def test_casos_sociales_secuencia_tres_etapas(self):
        caso = CasoSocial.objects.create(
            codigo_caso="CASO-TEST-001", persona_anonimizada="Persona Prueba",
            delegacion=self.delegacion
        )
        AtencionSocial.objects.create(caso=caso, etapa=1, tipo_atencion="Ficha", detalle="E1", resultado="OK", funcionario=self.usuario)
        AtencionSocial.objects.create(caso=caso, etapa=2, tipo_atencion="Visita", detalle="E2", resultado="OK", funcionario=self.usuario)
        AtencionSocial.objects.create(caso=caso, etapa=3, tipo_atencion="Cierre", detalle="E3", resultado="OK", funcionario=self.usuario)
        self.assertEqual(caso.atenciones.count(), 3)

    # 7. Auditoría de Operaciones Críticas (RF-036, CA-09, HU-30)
    def test_ca09_auditoria_operaciones(self):
        RegistroAuditoria.objects.create(
            usuario=self.usuario, accion='CREAR', entidad='Actividad',
            identificador_registro="101", valor_nuevo={'actividad': 'Nueva'}
        )
        self.assertEqual(RegistroAuditoria.objects.filter(identificador_registro="101").count(), 1)

    # 8. Verificación de Vistas Web y RBAC (HTTP 200 y control de acceso)
    def test_vistas_web_integrales(self):
        c = Client()
        # Login
        r_login = c.get('/')
        self.assertEqual(r_login.status_code, 200)

        # Autenticar como delegado
        c.force_login(self.verificador)

        for url in ['/dashboard/', '/personal/', '/verificacion/', '/tubo/', '/terreno/', '/casos-sociales/']:
            resp = c.get(url)
            self.assertEqual(resp.status_code, 200, f"Falla en {url}")

        # Delegado no debe acceder a auditoría (Segregación RNF-008, debe dar 302 redirect)
        resp_auditoria_del = c.get('/auditoria/')
        self.assertEqual(resp_auditoria_del.status_code, 302)

        # Admin sí puede acceder a auditoría (HTTP 200)
        admin_user = Usuario.objects.create_user(
            username='admin.test', password='password123',
            rol=Usuario.Rol.ADMINISTRADOR
        )
        c.force_login(admin_user)
        resp_auditoria_admin = c.get('/auditoria/')
        self.assertEqual(resp_auditoria_admin.status_code, 200)

    # 10. Seguridad y Segregación de Funciones en API REST (RN-009, CA-07)
    def test_segregacion_funciones_api(self):
        c = Client()
        act = Actividad.objects.create(
            funcionario=self.usuario, delegacion=self.delegacion,
            item_medicion=self.item1, actividad_solicitud="Operativo Terreno",
            accion_ejecutada="Realizado", fecha_actividad=date(2026, 8, 1)
        )
        ev = Evidencia.objects.create(actividad=act, estado=Evidencia.Estado.PENDIENTE)

        # 1. Funcionario intenta auto-aprobarse -> Debe retornar HTTP 403 Forbidden
        c.force_login(self.usuario)
        resp_func = c.post(f"/api/evidencias/{ev.id}/validar/", {'decision': 'APROBADA'}, content_type='application/json')
        self.assertEqual(resp_func.status_code, 403)

        # 2. Delegado de su delegación aprueba -> Debe retornar HTTP 200 OK
        c.force_login(self.verificador)
        resp_del = c.post(f"/api/evidencias/{ev.id}/validar/", {'decision': 'APROBADA'}, content_type='application/json')
        self.assertEqual(resp_del.status_code, 200)
        ev.refresh_from_db()
        self.assertEqual(ev.estado, Evidencia.Estado.APROBADA)

    def test_api_evidence_correction_requires_reason_and_uses_authenticated_verifier(self):
        actividad = Actividad.objects.create(
            funcionario=self.usuario,
            delegacion=self.delegacion,
            item_medicion=self.item1,
            actividad_solicitud='Validación de evidencia',
            fecha_actividad=date(2026, 8, 1),
        )
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        admin = Usuario.objects.create_superuser(
            username='admin.spoof.validation',
            email='admin.validation@example.com',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        client = Client()
        client.force_login(self.usuario)

        forbidden = client.post(
            f'/api/evidencias/{evidencia.pk}/validar/',
            {
                'decision': Evidencia.Estado.APROBADA,
                'verificador': admin.pk,
            },
            content_type='application/json',
        )
        self.assertEqual(forbidden.status_code, 403)

        client.force_login(self.verificador)
        missing_reason = client.post(
            f'/api/evidencias/{evidencia.pk}/validar/',
            {'decision': Evidencia.Estado.CORRECCION},
            content_type='application/json',
        )
        self.assertEqual(missing_reason.status_code, 400)
        evidencia.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.PENDIENTE)

        response = client.post(
            f'/api/evidencias/{evidencia.pk}/validar/',
            {
                'decision': Evidencia.Estado.CORRECCION,
                'observacion': 'Adjuntar fotografía legible.',
                'verificador': admin.pk,
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        evidencia.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.CORRECCION)
        self.assertEqual(evidencia.verificador, self.verificador)
        self.assertIsNotNone(evidencia.fecha_validacion)
        self.assertEqual(
            evidencia.observacion_rechazo,
            'Adjuntar fotografía legible.',
        )
        audit = RegistroAuditoria.objects.get(
            entidad='Evidencia',
            identificador_registro=str(evidencia.pk),
            accion='VALIDAR_EVIDENCIA',
        )
        self.assertEqual(audit.funcionario, self.verificador)
        self.assertEqual(
            audit.valor_nuevo['verificador_id'],
            self.verificador.pk,
        )

    # 11. Registro de Evidencias de Terreno con Dirección y Múltiples Fotos
    def test_registro_evidencias_terreno_con_direccion_y_fotos(self):
        c = Client()
        c.force_login(self.usuario)
        payload = {
            'funcionario': self.usuario.id,
            'delegacion': self.delegacion.id,
            'item_medicion': self.item1.id,
            'actividad_solicitud': 'Inspección operativa: Balmaceda 1234',
            'accion_ejecutada': 'Bacheo y demarcación de calzada completado.',
            'direccion': 'Balmaceda 1234, La Serena',
            'url_foto_simulada': 'https://ejemplo.cl/foto1.jpg',
            'url_foto_simulada_2': 'https://ejemplo.cl/foto2.jpg',
            'url_foto_simulada_3': 'https://ejemplo.cl/foto3.jpg'
        }
        resp = c.post('/api/actividades/', payload, content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['direccion'], 'Balmaceda 1234, La Serena')
        self.assertIsNotNone(data['evidencia'])
        self.assertEqual(data['evidencia']['url_foto_simulada_2'], 'https://ejemplo.cl/foto2.jpg')
        self.assertEqual(data['evidencia']['url_foto_simulada_3'], 'https://ejemplo.cl/foto3.jpg')

    # 12. Métodos del Diagrama de Clases UML (Documento de Soledad Valdebenito)
    def test_metodos_diagrama_clases_soledad(self):
        from sgr_app.models import (
            Actividad, Compromiso, Evidencia, Solicitante, Funcionario,
            Auditoria, Rol, Delegacion, Cargo, Area, Item, Meta, Periodo,
            Indicador, AjusteIncentivo
        )

        # 1. Rol y permisos
        rol_coord = Rol.objects.create(nombre="SUPERVISOR", descripcion="Supervisor Comunal")
        self.assertTrue(rol_coord.tienePermiso("GESTIONAR_COORDINACION"))
        self.assertFalse(rol_coord.tienePermiso("PERMISO_INEXISTENTE"))

        # 2. Area
        area_ops = Area.objects.create(nombre="Operaciones y Territorio")
        self.assertTrue(area_ops.activarArea())
        self.assertEqual(area_ops.obtenerCantidadFuncionarios(), 0)

        # 3. Delegacion: activar, desactivar, cumplimiento, semaforo
        self.assertTrue(self.delegacion.activarDelegacion())
        res_sem = self.delegacion.obtenerResumenSemaforo()
        self.assertIn("VERDE", res_sem)

        # 4. Cargo: activar, desactivar
        self.assertTrue(self.cargo.activarCargo())

        # 5. Solicitante y validarRun Módulo 11
        sol = Solicitante.objects.create(run="11.111.111-1", nombre="Juan Pérez")
        # RUN válido de prueba con el formato chileno requerido.
        self.assertTrue(sol.validarRun())
        historial = sol.obtenerHistorial()
        self.assertIsNotNone(historial)

        # 6. Funcionario (Usuario): es_admin, validarRun, registrarActividad, verDashboardPersonal
        func = self.usuario
        func.rut_anonimizado = "11.111.111-1"
        func.save()
        self.assertTrue(func.validarRun())
        self.assertFalse(func.es_admin())
        dash = func.verDashboardPersonal()
        self.assertIn("funcionario", dash)
        self.assertIn("cumplimiento_general", dash)

        # 7. Actividad: asociarSolicitante, generarCompromiso, adjuntarEvidencia
        act = func.registrarActividad(
            fecha_solicitud=date(2026, 8, 1),
            solicitud_problema="Luminaria apagada",
            accion="Reparación luminaria",
            item=self.item1,
            solicitante=sol
        )
        act.asociarSolicitante(sol)
        comp = act.generarCompromiso(
            origen="Vecino Central",
            territorio=self.delegacion,
            fecha_compromiso=date(2026, 8, 20),
            area_apoyo="Alumbrado",
            observacion="Urgente"
        )
        self.assertEqual(comp.solicitante, "Vecino Central")
        evid = act.adjuntarEvidencia("https://ejemplo.cl/evidencia.jpg", descripcion="Foto luminaria")
        self.assertEqual(evid.estado, Evidencia.Estado.PENDIENTE)

        # 8. Compromiso: actualizarEstado, fechaVencida, reprogramarFecha, marcarCompletado
        comp.actualizarEstado("PENDIENTE")
        self.assertTrue(comp.fechaVencida())
        self.assertGreater(comp.calcularDiasAtraso(), 0)
        comp.reprogramarFecha(date(2026, 12, 31), motivo="Falta repuesto")
        self.assertFalse(comp.fechaVencida())
        comp.marcarCompletado()
        self.assertEqual(comp.estado, CompromisoAgenda.Estado.COMPLETADO)

        # 9. Evidencia: cargarArchivo, solicitarValidacion, validarEvidencia, rechazarEvidencia
        evid.solicitarValidacion()
        self.assertEqual(evid.estado, Evidencia.Estado.PENDIENTE)
        evid.validarEvidencia("Aprobado por delegado")
        self.assertEqual(evid.estado, Evidencia.Estado.APROBADA)
        evid.rechazarEvidencia("Rechazado por foto borrosa")
        self.assertEqual(evid.estado, Evidencia.Estado.RECHAZADA)

        # 10. Periodo: evaluarCumplimiento, vigenciaFecha, estaActivo, calcularMetaEsperadaAlDia
        self.assertEqual(self.periodo.evaluarCumplimiento(95.0), "VERDE")
        self.assertEqual(self.periodo.evaluarCumplimiento(65.0), "AMARILLO")
        self.assertEqual(self.periodo.evaluarCumplimiento(30.0), "ROJO")
        self.assertTrue(self.periodo.vigenciaFecha(date(2026, 8, 1)))
        meta_esp = self.periodo.calcularMetaEsperadaAlDia(100.0)
        self.assertGreaterEqual(meta_esp, 0.0)

        # 11. Meta e Indicador
        meta = Meta.objects.create(
            periodo=self.periodo,
            funcionario=func,
            delegacion=self.delegacion,
            valorMeta=10.0,
            ponderador=30.0
        )
        self.assertEqual(meta.obtenerEstadoSemaforo(), "ROJO")
        self.assertTrue(meta.validarPonderacion())
        indicador = Indicador.objects.create(meta=meta, periodo=self.periodo, avance=8.0)
        indicador.actualizarAvance(9.0)
        self.assertEqual(indicador.avance, 9.0)

        # 12. AjusteIncentivo: aplicarAjuste, aprobarAjuste, rechazarAjuste
        admin_u = Usuario.objects.create_user(username="admin.ajustes", password="p",         rol=Usuario.Rol.ADMINISTRADOR)
        ajuste = AjusteIncentivo.objects.create(
            funcionario=func,
            periodo=self.periodo,
            tipo="BONIFICACION",
            valorPorcentaje=10.0,
            estado="PENDIENTE"
        )
        # Funcionario sin permisos lanza PermissionError
        with self.assertRaises(PermissionError):
            ajuste.aprobarAjuste(func)
        # Admin intentando auto-aprobarse su propio ajuste lanza ValueError
        ajuste_propio_admin = AjusteIncentivo.objects.create(
            funcionario=admin_u, periodo=self.periodo, tipo="BONIFICACION", valorPorcentaje=5.0, estado="PENDIENTE"
        )
        with self.assertRaises(ValueError):
            ajuste_propio_admin.aprobarAjuste(admin_u)
        # Admin sí puede aprobar ajuste de otro funcionario
        self.assertTrue(ajuste.aprobarAjuste(admin_u))
        self.assertEqual(ajuste.estado, "APROBADO")
        # Aplicar ajuste suma al porcentaje base
        self.assertEqual(ajuste.aplicarAjuste(80.0), 90.0)

        # 13. Auditoria: registrarAuditoria, consultarHistorial
        log = Auditoria.registrarAuditoria("TEST_EVENT", "Meta", meta.id, {"val": 1}, {"val": 2}, func)
        self.assertIsNotNone(log)
        hist = Auditoria.consultarHistorial("Meta", str(meta.id))
        self.assertEqual(hist.count(), 1)

    # =========================================================================
    # PLAN DE PRUEBAS UNITARIAS - MÓDULO AUTENTICACIÓN (CP-LOGIN-001 a 008)
    # =========================================================================
    def test_cp_login_001_credenciales_validas(self):
        """CP-LOGIN-001: Login con usuario y contraseña válidos -> Autenticación exitosa."""
        c = Client()
        resp = c.post('/login/', {'username': 'carlos.test', 'password': 'Password123!'}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/dashboard/', resp.url)

    def test_cp_login_002_password_incorrecta(self):
        """CP-LOGIN-002: Usuario válido y contraseña incorrecta -> Rechazado."""
        c = Client()
        resp = c.post('/login/', {'username': 'carlos.test', 'password': 'PasswordIncorrecta'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Credenciales inválidas')

    def test_cp_login_003_usuario_inexistente(self):
        """CP-LOGIN-003: Usuario inexistente -> Rechazado."""
        c = Client()
        resp = c.post('/login/', {'username': 'no.existe', 'password': 'Cualquiera123!'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Credenciales inválidas')

    def test_cp_login_004_usuario_vacio(self):
        """CP-LOGIN-004: Usuario vacío -> Validación de campo obligatorio."""
        c = Client()
        resp = c.post('/login/', {'username': '', 'password': 'Password123!'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'El nombre de usuario es obligatorio')

    def test_cp_login_005_password_vacio(self):
        """CP-LOGIN-005: Contraseña vacía -> Validación de campo obligatorio."""
        c = Client()
        resp = c.post('/login/', {'username': 'carlos.test', 'password': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'La contraseña es obligatoria')

    def test_cp_login_006_campos_vacios(self):
        """CP-LOGIN-006: Usuario y contraseña vacíos -> Ambos obligatorios."""
        c = Client()
        resp = c.post('/login/', {'username': '', 'password': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Usuario y contraseña son obligatorios')

    def test_cp_login_007_usuario_inactivo(self):
        """CP-LOGIN-007: Usuario inactivo/bloqueado -> Acceso rechazado."""
        Usuario.objects.create_user(
            username="bloqueado.test",
            password="Password123!",
            is_active=False
        )
        c = Client()
        resp = c.post('/login/', {'username': 'bloqueado.test', 'password': 'Password123!'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Usuario bloqueado o inactivo')

    def test_cp_login_008_formato_invalido_injection(self):
        """CP-LOGIN-008: Datos con formato o caracteres inválidos (SQL Injection/XSS) -> Rechazo seguro."""
        c = Client()
        resp = c.post('/login/', {'username': "' OR '1'='1", 'password': "' OR '1'='1"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Credenciales inválidas')

    # 21. Configuración y Conectividad Cloud AWS (Amazon S3 & Amazon RDS)
    def test_integracion_cloud_aws(self):
        """Verifica la arquitectura Cloud AWS especificada en Documentacion.pdf págs 80-84."""
        from sgr_app.services.aws_service import test_s3_connection, test_rds_connection, is_s3_enabled
        from django.conf import settings

        # 1. Diagnóstico de base de datos relacional (RDS/MySQL o fallback SQLite)
        res_rds = test_rds_connection()
        self.assertIn(res_rds['status'], ['connected', 'local_sqlite'])

        # 2. Diagnóstico de almacenamiento S3 (fallback local seguro si USE_S3=False)
        res_s3 = test_s3_connection()
        self.assertIn(res_s3['status'], ['connected', 'disabled'])

        # 3. Verificación de variables y settings de AWS
        self.assertTrue(hasattr(settings, 'USE_S3'))
        self.assertTrue(hasattr(settings, 'USE_MYSQL'))

    def test_dashboards_diferenciados_por_rol(self):
        client = Client()

        client.force_login(self.verificador)
        supervisor_response = client.get('/dashboard/')
        self.assertEqual(supervisor_response.status_code, 200)
        self.assertTemplateUsed(supervisor_response, 'dashboard_supervisor.html')

        administrador = Usuario.objects.create_user(
            username='admin.dashboard',
            password='Password123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        client.force_login(administrador)
        admin_response = client.get('/dashboard/')
        self.assertEqual(admin_response.status_code, 200)
        self.assertTemplateUsed(admin_response, 'dashboard_admin.html')

        client.force_login(self.usuario)
        administrativo_response = client.get('/dashboard/')
        self.assertEqual(administrativo_response.status_code, 200)
        self.assertTemplateUsed(administrativo_response, 'dashboard_administrativo.html')

    def test_delegado_solo_consulta_datos_de_su_territorio(self):
        otra_delegacion = Delegacion.objects.create(
            nombre='Delegación Centro',
            codigo='CEN',
        )
        otro_funcionario = Usuario.objects.create_user(
            username='funcionario.otra.delegacion',
            password='Password123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=self.cargo,
            delegacion=otra_delegacion,
        )
        actividad_local = Actividad.objects.create(
            funcionario=self.usuario,
            delegacion=self.delegacion,
            item_medicion=self.item1,
            actividad_solicitud='Actividad local',
            accion_ejecutada='Acción local',
            fecha_actividad=date(2026, 8, 1),
        )
        evidencia_local = Evidencia.objects.create(
            actividad=actividad_local,
            estado=Evidencia.Estado.PENDIENTE,
        )
        actividad_ajena = Actividad.objects.create(
            funcionario=otro_funcionario,
            delegacion=otra_delegacion,
            item_medicion=self.item1,
            actividad_solicitud='Actividad de otra delegación',
            accion_ejecutada='Acción ajena',
            fecha_actividad=date(2026, 8, 1),
        )
        evidencia_ajena = Evidencia.objects.create(
            actividad=actividad_ajena,
            estado=Evidencia.Estado.PENDIENTE,
        )
        compromiso_local = CompromisoAgenda.objects.create(
            solicitante='Solicitante local',
            delegacion=self.delegacion,
            responsable=self.usuario,
            fecha_comprometida=date(2026, 8, 20),
            descripcion='Compromiso local',
        )
        CompromisoAgenda.objects.create(
            solicitante='Solicitante de otra delegación',
            delegacion=otra_delegacion,
            responsable=otro_funcionario,
            fecha_comprometida=date(2026, 8, 20),
            descripcion='Compromiso ajeno',
        )
        caso_local = CasoSocial.objects.create(
            codigo_caso='CASO-LOCAL',
            persona_anonimizada='Persona local',
            delegacion=self.delegacion,
        )
        CasoSocial.objects.create(
            codigo_caso='CASO-AJENO',
            persona_anonimizada='Persona de otra delegación',
            delegacion=otra_delegacion,
        )

        client = Client()
        client.force_login(self.verificador)

        dashboard_response = client.get('/dashboard/')
        self.assertEqual(
            [delegacion.pk for delegacion in dashboard_response.context['delegaciones']],
            [self.delegacion.pk],
        )

        personal_response = client.get('/personal/')
        self.assertEqual(
            list(personal_response.context['todos_funcionarios'].values_list('pk', flat=True)),
            [self.usuario.pk],
        )

        verification_response = client.get('/verificacion/?estado=TODOS')
        self.assertEqual(
            list(verification_response.context['evidencias'].values_list('pk', flat=True)),
            [evidencia_local.pk],
        )

        terrain_response = client.get('/terreno/')
        self.assertEqual(
            list(terrain_response.context['actividades_existentes'].values_list('pk', flat=True)),
            [actividad_local.pk],
        )

        tube_response = client.get('/tubo/')
        self.assertEqual(
            [compromiso.pk for compromiso in tube_response.context['todos_los_compromisos']],
            [compromiso_local.pk],
        )

        cases_response = client.get('/casos-sociales/')
        self.assertEqual(
            list(cases_response.context['casos'].values_list('pk', flat=True)),
            [caso_local.pk],
        )

        validation_response = client.post(
            f'/verificacion/{evidencia_ajena.pk}/validar/',
            {'decision': Evidencia.Estado.APROBADA},
        )
        self.assertEqual(validation_response.status_code, 404)
        evidencia_ajena.refresh_from_db()
        self.assertEqual(evidencia_ajena.estado, Evidencia.Estado.PENDIENTE)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_completo_por_correo(self):
        usuario = Usuario.objects.create_user(
            username='recuperacion.test',
            password='OldPassword123!',
            email='recuperacion@example.com',
        )
        client = Client()

        form_response = client.get('/password-reset/')
        self.assertEqual(form_response.status_code, 200)
        self.assertTemplateUsed(form_response, 'registration/password_reset_form.html')

        submit_response = client.post(
            '/password-reset/',
            {'email': usuario.email},
        )
        self.assertRedirects(submit_response, '/password-reset/done/')
        done_response = client.get('/password-reset/done/')
        self.assertEqual(done_response.status_code, 200)
        self.assertTemplateUsed(done_response, 'registration/password_reset_done.html')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [usuario.email])
        self.assertTrue(mail.outbox[0].alternatives)
        self.assertEqual(mail.outbox[0].alternatives[0].mimetype, 'text/html')

        match = re.search(
            r'/reset/([^/]+)/([^/\s]+)',
            mail.outbox[0].body,
        )
        self.assertIsNotNone(match)
        uidb64, token = match.groups()

        confirm_response = client.get(f'/reset/{uidb64}/{token}/', follow=True)
        self.assertEqual(confirm_response.status_code, 200)
        self.assertTemplateUsed(confirm_response, 'registration/password_reset_confirm.html')
        complete_response = client.post(
            f'/reset/{uidb64}/set-password/',
            {
                'new_password1': 'NewSecurePassword456!',
                'new_password2': 'NewSecurePassword456!',
            },
        )
        self.assertRedirects(complete_response, '/reset/complete/')
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password('NewSecurePassword456!'))
        self.assertFalse(usuario.check_password('OldPassword123!'))

        final_response = client.get('/reset/complete/')
        self.assertEqual(final_response.status_code, 200)
        self.assertTemplateUsed(final_response, 'registration/password_reset_complete.html')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_no_revela_correos_no_registrados(self):
        client = Client()
        response = client.post(
            '/password-reset/',
            {'email': 'no-existe@example.com'},
        )
        self.assertRedirects(response, '/password-reset/done/')
        self.assertEqual(mail.outbox, [])

    def test_mantenedores_delegaciones_y_cargos_requieren_administrador(self):
        client = Client()
        client.force_login(self.verificador)
        response = client.get('/mantenedores/delegaciones/')
        self.assertEqual(response.status_code, 403)

    def test_mantenedor_delegaciones_busca_y_desactiva_sin_borrar(self):
        administrador = Usuario.objects.create_user(
            username='admin.mantenedores',
            password='Password123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        client = Client()
        client.force_login(administrador)

        list_response = client.get('/mantenedores/delegaciones/?q=CIA')
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.delegacion.nombre)
        self.assertEqual(list_response.context['object_list'].count(), 1)

        delete_response = client.post(
            f'/mantenedores/delegaciones/{self.delegacion.pk}/desactivar/',
        )
        self.assertRedirects(delete_response, '/mantenedores/delegaciones/')
        self.delegacion.refresh_from_db()
        self.assertFalse(self.delegacion.activo)
        self.assertTrue(Delegacion.objects.filter(pk=self.delegacion.pk).exists())
        self.assertTrue(RegistroAuditoria.objects.filter(
            entidad='Delegacion',
            identificador_registro=str(self.delegacion.pk),
            accion='BAJA_LOGICA',
        ).exists())

        inactive_response = client.get('/mantenedores/delegaciones/')
        self.assertEqual(inactive_response.context['object_list'].count(), 0)
        self.assertEqual(
            client.get(
                f'/mantenedores/delegaciones/{self.delegacion.pk}/modificar/',
            ).status_code,
            404,
        )

    def test_mantenedor_delegaciones_crea_y_modifica_registros(self):
        administrador = Usuario.objects.create_user(
            username='admin.delegaciones.crud',
            password='Password123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        client = Client()
        client.force_login(administrador)

        create_response = client.post(
            '/mantenedores/delegaciones/crear/',
            {
                'nombre': 'Delegación Norte',
                'codigo': 'NOR',
                'direccion': 'Av. Norte 100',
                'telefono': '',
                'email': 'norte@example.com',
                'ambito': 'Urbano',
            },
        )
        self.assertRedirects(create_response, '/mantenedores/delegaciones/')
        delegacion = Delegacion.objects.get(codigo='NOR')

        update_response = client.post(
            f'/mantenedores/delegaciones/{delegacion.pk}/modificar/',
            {
                'nombre': 'Delegación Norte Actualizada',
                'codigo': 'NOR',
                'direccion': 'Av. Norte 200',
                'telefono': '',
                'email': 'norte@example.com',
                'ambito': 'Urbano',
            },
        )
        self.assertRedirects(update_response, '/mantenedores/delegaciones/')
        delegacion.refresh_from_db()
        self.assertEqual(delegacion.nombre, 'Delegación Norte Actualizada')
        self.assertEqual(delegacion.direccion, 'Av. Norte 200')

    def test_mantenedor_soft_delete_revierte_estado_si_falla_auditoria(self):
        administrador = Usuario.objects.create_user(
            username='admin.softdelete.rollback',
            password='Password123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        client = Client()
        client.force_login(administrador)

        with patch(
            'sgr_app.mantenedores.RegistroAuditoria.objects.create',
            side_effect=IntegrityError('audit insert failed'),
        ):
            with self.assertRaises(IntegrityError):
                client.post(
                    f'/mantenedores/delegaciones/{self.delegacion.pk}/desactivar/',
                )

        self.delegacion.refresh_from_db()
        self.assertTrue(self.delegacion.activo)
        self.assertTrue(Delegacion.objects.filter(pk=self.delegacion.pk).exists())
        self.assertFalse(RegistroAuditoria.objects.filter(
            entidad='Delegacion',
            identificador_registro=str(self.delegacion.pk),
            accion='BAJA_LOGICA',
        ).exists())

    def test_mantenedor_cargos_crea_actualiza_y_desactiva_logicamente(self):
        administrador = Usuario.objects.create_user(
            username='admin.cargos',
            password='Password123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        client = Client()
        client.force_login(administrador)

        create_response = client.post(
            '/mantenedores/cargos/crear/',
            {'nombre': 'Inspector Territorial', 'descripcion': 'Cargo de prueba'},
        )
        self.assertRedirects(create_response, '/mantenedores/cargos/')
        cargo = Cargo.objects.get(nombre='Inspector Territorial')

        update_response = client.post(
            f'/mantenedores/cargos/{cargo.pk}/modificar/',
            {'nombre': 'Inspector de Terreno', 'descripcion': 'Cargo actualizado'},
        )
        self.assertRedirects(update_response, '/mantenedores/cargos/')
        cargo.refresh_from_db()
        self.assertEqual(cargo.nombre, 'Inspector de Terreno')

        delete_response = client.post(
            f'/mantenedores/cargos/{cargo.pk}/desactivar/',
        )
        self.assertRedirects(delete_response, '/mantenedores/cargos/')
        cargo.refresh_from_db()
        self.assertFalse(cargo.activo)
        self.assertTrue(Cargo.objects.filter(pk=cargo.pk).exists())
        self.assertFalse(
            Cargo.objects.filter(pk=cargo.pk, activo=True).exists()
        )