"""Tableros por rol y gestión territorial (Panel Territorial, comparación, informes, vencimientos)."""

import json
from datetime import date, timedelta

from django.test import Client, TestCase
from django.utils import timezone

from sgr_app.models import Actividad, CompromisoAgenda, Delegacion, PeriodoMedicion, Usuario
from sgr_app.services.panel_territorial import comparacion_territorial, panel_territorial

PASSWORD = 'TestPassword123!'


class PanelesTerritorialesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hoy = timezone.localdate()
        cls.periodo = PeriodoMedicion.objects.create(
            nombre='Período paneles', fecha_inicio=cls.hoy - timedelta(days=40),
            fecha_termino=cls.hoy + timedelta(days=40),
        )
        cls.del_a = Delegacion.objects.create(nombre='Las Compañías', codigo='CIA')
        cls.del_b = Delegacion.objects.create(nombre='La Pampa', codigo='PAM')
        cls.admin = Usuario.objects.create_superuser(
            username='p.admin', email='p.admin@example.com', password=PASSWORD, rol=Usuario.Rol.ADMINISTRADOR,
        )
        cls.supervisor = Usuario.objects.create_user(username='p.sup', password=PASSWORD, rol=Usuario.Rol.SUPERVISOR)
        cls.delegado = Usuario.objects.create_user(
            username='p.del', password=PASSWORD, rol=Usuario.Rol.SUPERVISOR, es_delegado=True, delegacion=cls.del_a,
        )
        cls.func_a = Usuario.objects.create_user(
            username='p.func.a', password=PASSWORD, rol=Usuario.Rol.ADMINISTRATIVO, delegacion=cls.del_a,
        )
        cls.func_b = Usuario.objects.create_user(
            username='p.func.b', password=PASSWORD, rol=Usuario.Rol.ADMINISTRATIVO, delegacion=cls.del_b,
        )
        # Delegación A: 1 realizado en plazo, 1 realizado fuera de plazo, 1 vencido, 1 por vencer.
        cls.c_en_plazo = cls._compromiso(cls.del_a, cls.func_a, 'Alumbrado Público', -10, realizado_dias=-12)
        cls.c_fuera = cls._compromiso(cls.del_a, cls.func_a, 'Alumbrado Público', -10, realizado_dias=-5)
        cls.c_vencido = cls._compromiso(cls.del_a, cls.func_a, 'Aseo y Ornato', -3)
        cls.c_proximo = cls._compromiso(cls.del_a, cls.func_a, 'Aseo y Ornato', 1)
        # Delegación B: un área que la delegación A nunca debe ver.
        cls._compromiso(cls.del_b, cls.func_b, 'Unidad Exclusiva Pampa', -2)
        Actividad.objects.create(
            funcionario=cls.func_a, delegacion=cls.del_a, fecha_actividad=cls.hoy,
            latitud=-29.9, longitud=-71.25, actividad_solicitud='Poste caído', direccion='Calle privada 123',
        )
        Actividad.objects.create(
            funcionario=cls.func_b, delegacion=cls.del_b, fecha_actividad=cls.hoy,
            latitud=-29.95, longitud=-71.3, actividad_solicitud='Solicitud de otra delegación',
        )

    @classmethod
    def _compromiso(cls, delegacion, responsable, area, dias_plazo, realizado_dias=None):
        compromiso = CompromisoAgenda.objects.create(
            delegacion=delegacion, responsable=responsable, solicitante='Vecino', territorio='Sector Norte',
            area_apoyo=area, fecha_comprometida=cls.hoy + timedelta(days=30),
        )
        cambios = {
            'fecha_comprometida': cls.hoy + timedelta(days=dias_plazo),
            'fecha_compromiso': cls.hoy + timedelta(days=dias_plazo),
        }
        if realizado_dias is not None:
            cambios.update(
                estado=CompromisoAgenda.Estado.REALIZADO,
                fecha_realizacion=timezone.now() + timedelta(days=realizado_dias),
            )
        CompromisoAgenda.objects.filter(pk=compromiso.pk).update(**cambios)
        CompromisoAgenda.objects.filter(pk=compromiso.pk).update(fecha_registro=timezone.now() - timedelta(days=20))
        return compromiso

    def _cliente(self, usuario):
        client = Client()
        client.force_login(usuario)
        return client

    # --- Plantilla por rol ------------------------------------------------------------

    def test_cada_rol_recibe_su_tablero(self):
        esperado = {
            self.admin: 'dashboard_admin.html',
            self.supervisor: 'dashboard_supervisor.html',
            self.delegado: 'dashboard_delegado.html',
            self.func_a: 'dashboard_administrativo.html',
        }
        for usuario, plantilla in esperado.items():
            with self.subTest(usuario=usuario.username):
                response = self._cliente(usuario).get('/dashboard/')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.templates[0].name, plantilla)

    # --- Indicadores --------------------------------------------------------------

    def test_indicadores_de_plazo_y_respuesta(self):
        panel = panel_territorial(self.periodo, self.del_a, self.hoy)
        r = panel['resumen']
        self.assertEqual((r['total'], r['realizados'], r['vencidos'], r['abiertos']), (4, 2, 1, 2))
        self.assertEqual((r['en_plazo'], r['fuera_plazo'], r['pct_en_plazo']), (1, 1, 50.0))
        self.assertEqual(r['solicitudes'], 1)
        self.assertIsNotNone(r['dias_respuesta'])
        areas = {fila['area_apoyo']: fila for fila in panel['por_area']}
        self.assertEqual(areas['Aseo y Ornato']['vencidos'], 1)
        self.assertEqual([c.pk for c in panel['proximos_vencer']], [self.c_proximo.pk])

    def test_comparacion_ordena_por_vencidos(self):
        filas = comparacion_territorial(self.periodo, [self.del_b, self.del_a], self.hoy)
        self.assertEqual([f['delegacion'] for f in filas], [self.del_a, self.del_b])

    # --- Aislamiento del delegado ----------------------------------------------------

    def test_panel_del_delegado_solo_muestra_su_delegacion(self):
        response = self._cliente(self.delegado).get('/dashboard/')
        self.assertContains(response, 'Gestión del territorio')
        self.assertContains(response, 'Aseo y Ornato')
        self.assertNotContains(response, 'Unidad Exclusiva Pampa')
        self.assertNotIn('comparacion_territorial', response.context)
        self.assertNotContains(response, 'Gestión territorial por delegación')

    def test_delegado_no_puede_ver_otra_delegacion_manipulando_la_url(self):
        response = self._cliente(self.delegado).get(f'/dashboard/?delegacion_id={self.del_b.pk}')
        self.assertNotContains(response, 'Unidad Exclusiva Pampa')

    def test_mapa_solo_expone_coordenadas_y_fecha(self):
        response = self._cliente(self.delegado).get('/dashboard/')
        puntos = response.context['territorial']['puntos_mapa']
        self.assertEqual(len(puntos), 1)
        self.assertEqual(set(puntos[0]), {'lat', 'lng', 'fecha'})
        self.assertNotContains(response, 'Calle privada 123')
        datos = json.loads(response.content.decode().split('id="puntos-mapa-territorial" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(datos[0]['lat'], -29.9)

    def test_supervisor_y_admin_ven_la_comparacion_de_todas_las_delegaciones(self):
        for usuario in (self.supervisor, self.admin):
            with self.subTest(usuario=usuario.username):
                response = self._cliente(usuario).get('/dashboard/')
                self.assertContains(response, 'Gestión territorial por delegación')
                self.assertContains(response, 'Las Compañías')
                self.assertContains(response, 'La Pampa')

    # --- Informes del delegado ----------------------------------------------------

    def test_delegado_accede_a_informes_de_su_delegacion(self):
        client = self._cliente(self.delegado)
        response = client.get(f'/informes/?periodo={self.periodo.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Poste caído')
        self.assertNotContains(response, 'Solicitud de otra delegación')

    def test_delegado_no_obtiene_otra_delegacion_en_informe_ni_csv(self):
        client = self._cliente(self.delegado)
        for formato in ('', '&formato=csv'):
            with self.subTest(formato=formato or 'html'):
                response = client.get(f'/informes/?periodo={self.periodo.pk}&delegacion={self.del_b.pk}{formato}')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('Solicitud de otra delegación', response.content.decode('utf-8'))

    def test_funcionario_sigue_sin_acceso_a_informes(self):
        self.assertEqual(self._cliente(self.func_a).get('/informes/').status_code, 403)

    # --- Próximos vencimientos del funcionario --------------------------------------

    def test_funcionario_ve_compromisos_por_vencer(self):
        response = self._cliente(self.func_a).get('/dashboard/')
        self.assertContains(response, 'Compromisos por vencer')
        self.assertEqual([c.pk for c in response.context['vencimientos']['proximos']], [self.c_proximo.pk])
        self.assertEqual(response.context['vencimientos']['vencidos'], 1)
