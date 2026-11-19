"""Casos de uso que estaban parciales o sin cumplir (ver «Especificación de casos de uso»).

Cada prueba indica el CU que verifica.
"""
from datetime import date, timedelta
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from sgr_app.forms import ActividadFotosField
from sgr_app.models import (
    Actividad,
    AlertaRevisada,
    Area,
    Cargo,
    CasoSocial,
    CompromisoAgenda,
    Delegacion,
    Evidencia,
    ItemMedicion,
    PeriodoMedicion,
    RegistroAuditoria,
    ReglaAlerta,
    TipoAtencionSocial,
    Usuario,
)

PASSWORD = 'TestPassword123!'


def _imagen(nombre):
    buffer = BytesIO()
    Image.new('RGB', (2, 2), color='blue').save(buffer, format='JPEG')
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/jpeg')


class CasosDeUsoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hoy = timezone.localdate()
        inicio_mes = ((cls.hoy.month - 1) // 3) * 3 + 1
        cls.inicio = date(cls.hoy.year, inicio_mes, 1)
        fin_mes = inicio_mes + 3
        cls.fin = (
            date(cls.hoy.year + 1, 1, 1) if fin_mes == 13 else date(cls.hoy.year, fin_mes, 1)
        ) - timedelta(days=1)
        cls.del_a = Delegacion.objects.create(nombre='Delegación A', codigo='CUA')
        cls.del_b = Delegacion.objects.create(nombre='Delegación B', codigo='CUB')
        cls.area_social = Area.objects.create(nombre='Área social')
        TipoAtencionSocial.objects.create(area=cls.area_social, nombre='Ayuda social')
        cls.area_otra = Area.objects.create(nombre='Área operaciones')
        cls.cargo = Cargo.objects.create(nombre='Gestor territorial')
        cls.periodo = PeriodoMedicion.objects.create(
            nombre='Período actual', fecha_inicio=cls.inicio, fecha_termino=cls.fin,
        )
        ItemMedicion.objects.create(
            cargo=cls.cargo, periodo=cls.periodo, nombre_item='Visitas',
            meta_cuantitativa=10, ponderador=100,
        )
        crear = Usuario.objects.create_user
        cls.admin = crear(username='cu.admin', password=PASSWORD, rol=Usuario.Rol.ADMINISTRADOR)
        cls.coord = crear(username='cu.coord', password=PASSWORD, rol=Usuario.Rol.COORDINADOR)
        cls.delegado = crear(
            username='cu.delegado', password=PASSWORD, rol=Usuario.Rol.DELEGADO,
            delegacion=cls.del_a,
        )
        cls.func = crear(
            username='cu.func', password=PASSWORD, rol=Usuario.Rol.FUNCIONARIO,
            delegacion=cls.del_a, cargo=cls.cargo, area_entidad=cls.area_otra,
            first_name='Ana', last_name='Funcionaria',
        )
        cls.func2 = crear(
            username='cu.func2', password=PASSWORD, rol=Usuario.Rol.FUNCIONARIO,
            delegacion=cls.del_a, cargo=cls.cargo, first_name='Beto', last_name='Colega',
        )
        cls.func_social = crear(
            username='cu.social', password=PASSWORD, rol=Usuario.Rol.FUNCIONARIO,
            delegacion=cls.del_a, area_entidad=cls.area_social,
        )
        cls.consulta = crear(username='cu.consulta', password=PASSWORD, rol=Usuario.Rol.CONSULTA)
        cls.act_a = Actividad.objects.create(
            funcionario=cls.func, delegacion=cls.del_a, fecha_actividad=cls.hoy,
        )
        cls.act_b = Actividad.objects.create(
            funcionario=cls.func2, delegacion=cls.del_b, fecha_actividad=cls.hoy,
        )

    def _c(self, usuario):
        cliente = Client()
        cliente.force_login(usuario)
        return cliente

    # --- CU-26 -------------------------------------------------------------
    def test_cu26_delegado_anula_actividad_de_su_delegacion(self):
        delegado = self._c(self.delegado)
        delegado.post(f'/personal/actividades/{self.act_a.pk}/anular/', {'motivo': 'Duplicada'})
        self.act_a.refresh_from_db()
        self.assertTrue(self.act_a.anulada)
        respuesta = delegado.post(f'/personal/actividades/{self.act_b.pk}/anular/', {'motivo': 'x'})
        self.assertEqual(respuesta.status_code, 404)
        api = delegado.post(f'/api/v1/actividades/{self.act_b.pk}/anular/', {'motivo': 'x'})
        self.assertEqual(api.status_code, 404)

    # --- CU-10/11/12 y CU-08 --------------------------------------------------
    def test_cu10_11_12_coordinador_configura_y_cu08_solo_admin_reabre_con_motivo(self):
        coord = self._c(self.coord)
        self.assertEqual(coord.get('/mantenedores/ponderaciones/').status_code, 200)
        self.assertEqual(coord.get('/mantenedores/periodos/crear/').status_code, 200)
        self.assertEqual(coord.get(f'/mantenedores/periodos/{self.periodo.pk}/modificar/').status_code, 200)

        cerrado = PeriodoMedicion.objects.create(
            nombre='Cerrado', fecha_inicio=self.inicio, fecha_termino=self.fin, cerrado=True,
        )
        datos = {
            'nombre': cerrado.nombre, 'anio': self.inicio.year,
            'trimestre': (self.inicio.month - 1) // 3 + 1,
            'fecha_inicio': self.inicio.isoformat(), 'fecha_termino': self.fin.isoformat(),
            'dias_computables': cerrado.dias_computables or 1,
            'umbral_semaforo_verde': cerrado.umbral_semaforo_verde,
            'umbral_semaforo_ambar': cerrado.umbral_semaforo_ambar,
            'umbral_colectivo': cerrado.umbral_colectivo,
            'tope_cumplimiento': cerrado.tope_cumplimiento,
            'estado': 'on',
        }
        url = f'/mantenedores/periodos/{cerrado.pk}/modificar/'
        respuesta = coord.post(url, datos)
        self.assertContains(respuesta, 'Solo el Administrador puede reabrir', status_code=200)
        admin = self._c(self.admin)
        respuesta = admin.post(url, datos)
        self.assertContains(respuesta, 'Indica el motivo de la reapertura', status_code=200)
        admin.post(url, {**datos, 'motivo_reapertura': 'Corrección de datos'})
        cerrado.refresh_from_db()
        self.assertFalse(cerrado.cerrado)
        auditoria = RegistroAuditoria.objects.get(accion='REABRIR_PERIODO')
        self.assertEqual(auditoria.valor_nuevo['motivo_reapertura'], 'Corrección de datos')

    # --- CU-25 y CU-33 --------------------------------------------------------
    def test_cu25_reasignar_exige_motivo(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Vecino', delegacion=self.del_a, responsable=self.func,
            creado_por=self.func, fecha_comprometida=self.fin, observacion='Inicial',
        )
        datos = {
            'solicitante': 'Vecino', 'origen': 'EXT', 'territorio': 'Sector Centro',
            'delegacion': self.del_a.pk, 'responsable': self.func2.pk,
            'fecha_comprometida': self.fin.isoformat(), 'area_apoyo': 'Operaciones',
            'descripcion': 'x', 'observacion': 'Inicial',
        }
        respuesta = self._c(self.delegado).post(f'/tubo/{compromiso.pk}/editar/', datos)
        self.assertContains(respuesta, 'motivo de la reasignación')
        compromiso.refresh_from_db()
        self.assertEqual(compromiso.responsable, self.func)

    def test_cu33_agenda_compartida_con_filtros(self):
        propio = CompromisoAgenda.objects.create(
            solicitante='Propio', delegacion=self.del_a, responsable=self.func,
            creado_por=self.func, fecha_comprometida=self.fin, territorio='La Pampa',
        )
        CompromisoAgenda.objects.create(
            solicitante='De un colega', delegacion=self.del_a, responsable=self.func2,
            creado_por=self.func2, fecha_comprometida=self.fin, territorio='El Milagro',
        )
        CompromisoAgenda.objects.create(
            solicitante='Otra delegación', delegacion=self.del_b, responsable=self.func2,
            fecha_comprometida=self.fin,
        )
        func = self._c(self.func)
        pagina = func.get('/tubo/')
        self.assertContains(pagina, 'De un colega')
        self.assertNotContains(pagina, 'Otra delegación')
        self.assertContains(pagina, f'/tubo/{propio.pk}/anular/')
        self.assertContains(pagina, 'Solo lectura')
        filtrada = func.get('/tubo/', {'responsable': self.func2.pk})
        self.assertContains(filtrada, 'De un colega')
        self.assertNotContains(filtrada, 'Propio</')
        self.assertNotContains(func.get('/tubo/', {'territorio': 'La Pampa'}), 'De un colega')
        fecha = (self.fin + timedelta(days=1)).isoformat()
        self.assertNotContains(func.get('/tubo/', {'fecha_desde': fecha}), 'De un colega')

    # --- CU-29, CU-32, CU-39 --------------------------------------------------
    def test_cu29_maximo_tres_fotos_por_registro(self):
        campo = ActividadFotosField(required=False)
        with self.assertRaisesMessage(ValidationError, 'hasta 3 fotografías'):
            campo.clean([_imagen(f'{i}.jpg') for i in range(4)])

    def test_cu32_compromiso_sin_actividad_explica_como_cerrarlo(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Sin actividad', delegacion=self.del_a, responsable=self.func,
            creado_por=self.func, fecha_comprometida=self.fin,
        )
        with self.assertRaisesMessage(ValidationError, 'no tiene una actividad asociada'):
            compromiso.registrar_transicion(CompromisoAgenda.Estado.REALIZADO, self.func, 'Listo')

    def test_cu39_buscar_por_codigo_de_actividad_y_evidencia(self):
        evidencia = Evidencia.objects.create(actividad=self.act_a)
        coord = self._c(self.coord)
        por_actividad = coord.get('/informes/', {'q': self.act_a.codigo_registro, 'periodo': self.periodo.pk})
        self.assertIn(self.act_a, list(por_actividad.context['pagina']))
        por_evidencia = coord.get('/informes/', {'q': evidencia.codigo_verificador, 'periodo': self.periodo.pk})
        self.assertEqual([a.pk for a in por_evidencia.context['pagina']], [self.act_a.pk])

    # --- CU-01/02 y CU-18 -----------------------------------------------------
    def test_cu01_acceso_denegado_queda_auditado(self):
        self._c(self.func).post('/mantenedores/delegaciones/crear/', {})
        self._c(self.func).get('/api/v1/auditoria/')
        eventos = RegistroAuditoria.objects.filter(accion='ACCESO_DENEGADO', funcionario=self.func)
        rutas = {evento.valor_nuevo['ruta'] for evento in eventos}
        self.assertIn('/mantenedores/delegaciones/crear/', rutas)
        self.assertIn('/api/v1/auditoria/', rutas)

    def test_cu18_intento_de_alterar_auditoria_queda_registrado(self):
        registro = RegistroAuditoria.objects.create(
            funcionario=self.admin, accion='PRUEBA', entidad='Prueba', identificador='1',
        )
        with self.assertRaises(ValidationError):
            RegistroAuditoria.objects.filter(pk=registro.pk).delete()
        intento = RegistroAuditoria.objects.get(accion='INTENTO_ALTERAR_AUDITORIA')
        self.assertEqual(intento.valor_nuevo['operacion'], 'DELETE')
        self.assertEqual(intento.valor_nuevo['registros'], [str(registro.pk)])

    # --- CU-13, CU-17, CU-22 --------------------------------------------------
    def test_cu13_ficha_muestra_formula_aplicada(self):
        pagina = self._c(self.func).get('/personal/', {'periodo': self.periodo.pk})
        self.assertContains(pagina, '0 / 10,0 × 100')

    def test_cu17_comparacion_de_periodos_cerrados(self):
        PeriodoMedicion.objects.create(
            nombre='Trimestre anterior', fecha_inicio=self.inicio, fecha_termino=self.fin, cerrado=True,
        )
        self.assertEqual(self._c(self.func).get('/informes/brechas-historicas/').status_code, 403)
        pagina = self._c(self.coord).get('/informes/brechas-historicas/')
        self.assertContains(pagina, 'Trimestre anterior')
        self.assertContains(pagina, 'no toma decisiones automáticas')

    def test_cu22_panel_territorial_enlaza_a_registros(self):
        pagina = self._c(self.delegado).get('/dashboard/')
        self.assertContains(pagina, f'/tubo/?periodo={self.periodo.pk}&amp;estado=VENCIDO')
        self.assertContains(pagina, f'/informes/?periodo={self.periodo.pk}')

    # --- CU-07 y CU-37 --------------------------------------------------------
    def test_cu07_reglas_versionadas_y_cu37_alerta_revisada(self):
        CompromisoAgenda.objects.create(
            solicitante='Plazo', delegacion=self.del_a, responsable=self.func,
            creado_por=self.func, fecha_compromiso=self.hoy + timedelta(days=10),
            fecha_comprometida=self.hoy + timedelta(days=10), descripcion='Revisar luminaria',
        )
        func = self._c(self.func)
        url_alertas = f'/alertas/?periodo={self.periodo.pk}'
        sin_regla = func.get(url_alertas).context['alertas']
        self.assertFalse([a for a in sin_regla if a['tipo'] == 'PLAZO_PRÓXIMO'])

        self.assertEqual(self._c(self.coord).get('/mantenedores/reglas-alerta/').status_code, 403)
        admin = self._c(self.admin)
        admin.post('/mantenedores/reglas-alerta/', {
            'tipo': 'PLAZO_PROXIMO', 'activa': 'on', 'umbral': '15',
            'destinatarios': ['FUNCIONARIO', 'DELEGADO'],
        })
        admin.post('/mantenedores/reglas-alerta/', {
            'tipo': 'INACTIVIDAD', 'activa': 'on', 'umbral': '7', 'destinatarios': ['DELEGADO'],
        })
        self.assertEqual(ReglaAlerta.objects.count(), 2)
        self.assertTrue(RegistroAuditoria.objects.filter(accion='CONFIGURAR_REGLA_ALERTA').exists())

        alertas = func.get(url_alertas).context['alertas']
        plazo = [a for a in alertas if a['tipo'] == 'PLAZO_PRÓXIMO']
        self.assertEqual(len(plazo), 1)
        self.assertFalse([a for a in alertas if a['tipo'] == 'INACTIVIDAD'])

        func.post('/alertas/revisar/', {'clave': plazo[0]['clave']})
        revision = AlertaRevisada.objects.get(clave=plazo[0]['clave'])
        self.assertEqual(revision.revisada_por, self.func)
        alertas = func.get(url_alertas).context['alertas']
        self.assertTrue([a for a in alertas if a['revision']])
        pendientes = func.get(url_alertas + '&ver=pendientes').context['alertas']
        self.assertFalse([a for a in pendientes if a['tipo'] == 'PLAZO_PRÓXIMO'])

    # --- CU-28 ----------------------------------------------------------------
    def test_cu28_funcionario_social_registra_casos(self):
        self.assertEqual(self._c(self.func).get('/casos-sociales/').status_code, 403)
        social = self._c(self.func_social)
        self.assertEqual(social.get('/casos-sociales/').status_code, 200)
        social.post('/casos-sociales/', {
            'action': 'crear_caso', 'codigo_caso': 'CS-001', 'persona_anonimizada': 'P-001',
            'delegacion': self.del_a.pk, 'fecha_apertura': self.hoy.isoformat(),
        })
        self.assertTrue(CasoSocial.objects.filter(codigo_caso='CS-001', delegacion=self.del_a).exists())

    def test_cu28_area_territorial_accede_al_configurar_sus_tipos_de_atencion(self):
        # CU-28 «Funcionario (área social o territorial)»: el acceso se otorga por
        # configuración. Basta con que el área tenga tipos de atención social activos.
        funcionario = self._c(self.func)
        self.assertEqual(funcionario.get('/casos-sociales/').status_code, 403)
        tipo = TipoAtencionSocial.objects.create(
            area=self.area_otra, nombre='Orientación en terreno',
        )
        self.assertEqual(funcionario.get('/casos-sociales/').status_code, 200)
        tipo.activo = False
        tipo.save()
        self.assertEqual(funcionario.get('/casos-sociales/').status_code, 403)

    # --- CU-38 ----------------------------------------------------------------
    def test_cu38_usuario_de_consulta_solo_lee(self):
        consulta = self._c(self.consulta)
        for url in ('/dashboard/', '/informes/', '/tubo/', '/alertas/'):
            with self.subTest(url=url):
                self.assertEqual(consulta.get(url).status_code, 200)
        self.assertEqual(
            consulta.post(f'/personal/actividades/{self.act_a.pk}/anular/', {'motivo': 'x'}).status_code,
            403,
        )
        self.assertEqual(consulta.post('/api/v1/compromisos/', {}).status_code, 403)
        self.assertEqual(consulta.post('/tubo/crear/', {}).status_code, 403)
        # Tampoco ve los formularios de registro aunque escriba la URL.
        for url in (
            '/personal/registrar-actividad/',
            '/actividad-diaria/registrar-actividad/',
            '/actividad-diaria/registrar-solicitante/',
            '/terreno/',
            '/tubo/crear/',
        ):
            with self.subTest(formulario=url):
                self.assertEqual(consulta.get(url).status_code, 403)
        self.act_a.refresh_from_db()
        self.assertFalse(self.act_a.anulada)
        self.assertNotContains(consulta.get('/tubo/'), '/anular/')
        self.assertTrue(
            RegistroAuditoria.objects.filter(accion='ACCESO_DENEGADO', funcionario=self.consulta).exists()
        )

    # --- CU-15 y CU-14 --------------------------------------------------------
    def test_cu15_semaforo_muestra_actividad_y_destaca_inactivos(self):
        ReglaAlerta.objects.create(
            tipo='INACTIVIDAD', activa=True, umbral=0, destinatarios=['COORDINADOR'],
        )
        pagina = self._c(self.coord).get('/semaforo/')
        self.assertContains(pagina, 'Último registro')
        self.assertContains(pagina, 'Días sin registros')
        self.assertContains(pagina, 'Promedio diario')
        self.assertContains(pagina, 'data-inactivo="1"')
        filas = {f['id']: f for f in pagina.context['funcionarios_semaforo']}
        self.assertIn(self.func.pk, filas)
        fila = filas[self.func.pk]
        for clave in ('ultimo_registro', 'dias_sin_registro', 'total_registros', 'promedio_diario'):
            self.assertIn(clave, fila)
        self.assertTrue(fila['inactivo'])
        self.assertGreaterEqual(pagina.context['total_inactivos'], 1)

    def test_cu14_semaforo_muestra_brecha_en_puntos_porcentuales(self):
        pagina = self._c(self.coord).get('/semaforo/')
        self.assertContains(pagina, 'Brecha')
        for fila in pagina.context['funcionarios_semaforo']:
            self.assertEqual(
                fila['brecha_pp'], round(fila['avance_real'] - fila['objetivo_al_dia'], 2),
            )
        self.assertContains(pagina, ' pp')

    # --- CU-01 y CU-03 --------------------------------------------------------
    def test_cu01_desactivar_delegacion_con_funcionarios_exige_confirmar_aviso(self):
        admin = self._c(self.admin)
        url = f'/mantenedores/delegaciones/{self.del_a.pk}/desactivar/'
        self.assertContains(admin.get(url), 'Dependencias activas')
        admin.post(url, {})
        self.del_a.refresh_from_db()
        self.assertTrue(self.del_a.activo)
        admin.post(url, {'confirmar_dependencias': '1'})
        self.del_a.refresh_from_db()
        self.assertFalse(self.del_a.activo)
        auditoria = RegistroAuditoria.objects.filter(
            entidad='Delegacion', accion='BAJA_LOGICA', identificador=str(self.del_a.pk),
        ).get()
        self.assertTrue(auditoria.valor_nuevo['advertencias_confirmadas'])

    def test_cu01_delegacion_sin_dependencias_se_desactiva_sin_aviso(self):
        vacia = Delegacion.objects.create(nombre='Delegación vacía', codigo='CUV')
        admin = self._c(self.admin)
        url = f'/mantenedores/delegaciones/{vacia.pk}/desactivar/'
        self.assertNotContains(admin.get(url), 'Dependencias activas')
        admin.post(url, {})
        vacia.refresh_from_db()
        self.assertFalse(vacia.activo)

    def test_cu03_desactivar_cargo_con_funcionarios_e_items_exige_confirmar_aviso(self):
        admin = self._c(self.admin)
        url = f'/mantenedores/cargos/{self.cargo.pk}/desactivar/'
        pagina = admin.get(url)
        self.assertContains(pagina, 'funcionarios activos')
        self.assertContains(pagina, 'ítem de medición vigente')
        admin.post(url, {})
        self.cargo.refresh_from_db()
        self.assertTrue(self.cargo.activo)
        admin.post(url, {'confirmar_dependencias': '1'})
        self.cargo.refresh_from_db()
        self.assertFalse(self.cargo.activo)

    # --- CU-07 ----------------------------------------------------------------
    def test_cu07_cambio_de_regla_no_altera_alertas_ya_generadas(self):
        en_10_dias = self.hoy + timedelta(days=10)
        admin = self._c(self.admin)
        admin.post('/mantenedores/reglas-alerta/', {
            'tipo': 'PLAZO_PROXIMO', 'activa': 'on', 'umbral': '15', 'destinatarios': ['FUNCIONARIO'],
        })
        anterior = CompromisoAgenda.objects.create(
            solicitante='Antes del cambio', delegacion=self.del_a, responsable=self.func,
            creado_por=self.func, fecha_compromiso=en_10_dias, fecha_comprometida=en_10_dias,
            descripcion='Generada con 15 días',
        )
        func = self._c(self.func)
        # Período que contiene la fecha comprometida (las alertas se filtran por período).
        periodo_alerta, _ = PeriodoMedicion.objects.get_or_create(
            nombre='Período alertas CU-07',
            defaults={
                'fecha_inicio': self.hoy,
                'fecha_termino': self.hoy + timedelta(days=30),
            },
        )
        url = f'/alertas/?periodo={periodo_alerta.pk}'

        def plazos():
            return {
                a['clave'] for a in func.get(url).context['alertas'] if a['tipo'] == 'PLAZO_PRÓXIMO'
            }

        clave_anterior = f'PLAZO_PROXIMO:compromiso:{anterior.pk}:{en_10_dias.isoformat()}'
        self.assertIn(clave_anterior, plazos())

        # El Administrador acorta el plazo a 5 días: la alerta ya generada se mantiene.
        admin.post('/mantenedores/reglas-alerta/', {
            'tipo': 'PLAZO_PROXIMO', 'activa': 'on', 'umbral': '5', 'destinatarios': ['FUNCIONARIO'],
        })
        nuevo = CompromisoAgenda.objects.create(
            solicitante='Después del cambio', delegacion=self.del_a, responsable=self.func,
            creado_por=self.func, fecha_compromiso=en_10_dias, fecha_comprometida=en_10_dias,
            descripcion='Se evalúa con 5 días',
        )
        claves = plazos()
        self.assertIn(clave_anterior, claves)
        self.assertNotIn(f'PLAZO_PROXIMO:compromiso:{nuevo.pk}:{en_10_dias.isoformat()}', claves)

        # La alerta se resuelve cuando su condición deja de cumplirse (compromiso cerrado).
        CompromisoAgenda.objects.filter(pk=anterior.pk).update(estado=CompromisoAgenda.Estado.REALIZADO)
        self.assertNotIn(clave_anterior, plazos())

    # --- CU-09 ----------------------------------------------------------------
    def test_cu09_configuracion_se_aplica_a_periodos_futuros_seleccionados(self):
        inicio_futuro = self.fin + timedelta(days=1)
        futuro = PeriodoMedicion.objects.create(
            nombre='Período futuro', fecha_inicio=inicio_futuro,
            fecha_termino=inicio_futuro + timedelta(days=89),
        )
        no_elegido = PeriodoMedicion.objects.create(
            nombre='Período no elegido', fecha_inicio=inicio_futuro + timedelta(days=90),
            fecha_termino=inicio_futuro + timedelta(days=179),
        )
        antiguo = ItemMedicion.objects.create(
            cargo=self.cargo, periodo=futuro, nombre_item='Ítem antiguo',
            meta_cuantitativa=5, ponderador=100,
        )
        item = ItemMedicion.objects.get(cargo=self.cargo, periodo=self.periodo)
        coord = self._c(self.coord)
        pagina = coord.get('/mantenedores/ponderaciones/', {'cargo': self.cargo.pk, 'periodo': self.periodo.pk})
        self.assertContains(pagina, 'Período futuro')

        datos = {
            'cargo': self.cargo.pk, 'periodo': self.periodo.pk,
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk, 'items-0-nombre_item': 'Visitas', 'items-0-descripcion': '',
            'items-0-tipo_unidad': 'UNIDAD', 'items-0-meta_cuantitativa': '12',
            'items-0-ponderador': '100', 'items-0-unidad_medida': 'Actividades',
            'items-0-activo': 'on',
            'periodos_futuros': [futuro.pk],
        }
        coord.post('/mantenedores/ponderaciones/', datos)

        copiado = ItemMedicion.objects.get(cargo=self.cargo, periodo=futuro, nombre_item='Visitas')
        self.assertTrue(copiado.activo)
        self.assertEqual(copiado.meta_cuantitativa, 12)
        self.assertEqual(copiado.ponderador, 100)
        antiguo.refresh_from_db()
        self.assertFalse(antiguo.activo)  # se desactiva, no se borra
        self.assertFalse(ItemMedicion.objects.filter(periodo=no_elegido).exists())
        futuro.refresh_from_db()
        self.assertTrue(
            RegistroAuditoria.objects.filter(
                accion='VERSIONAR_PARAMETROS_PERIODO', identificador=str(futuro.pk),
            ).exists()
        )

    def test_cu09_no_replica_a_periodos_cerrados_ni_anteriores(self):
        cerrado = PeriodoMedicion.objects.create(
            nombre='Futuro cerrado', fecha_inicio=self.fin + timedelta(days=1),
            fecha_termino=self.fin + timedelta(days=90), cerrado=True,
        )
        item = ItemMedicion.objects.get(cargo=self.cargo, periodo=self.periodo)
        respuesta = self._c(self.coord).post('/mantenedores/ponderaciones/', {
            'cargo': self.cargo.pk, 'periodo': self.periodo.pk,
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk, 'items-0-nombre_item': 'Visitas',
            'items-0-tipo_unidad': 'UNIDAD', 'items-0-meta_cuantitativa': '10',
            'items-0-ponderador': '100', 'items-0-unidad_medida': 'Actividades',
            'items-0-activo': 'on', 'periodos_futuros': [cerrado.pk],
        })
        self.assertContains(respuesta, 'Solo puedes replicar a períodos futuros abiertos')
        self.assertFalse(ItemMedicion.objects.filter(periodo=cerrado).exists())
