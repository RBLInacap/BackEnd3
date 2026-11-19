"""Verificación de la matriz de permisos (RNF-005). Ver docs/MATRIZ_PERMISOS.md.

Cada prueba recorre los 4 perfiles y comprueba visibilidad, operaciones
permitidas y rechazos, incluidos intentos fuera de la delegación propia.
"""

from django.test import Client, TestCase
from django.utils import timezone

from sgr_app.models import Actividad, Cargo, CompromisoAgenda, Delegacion, Evidencia, Usuario

PASSWORD = 'TestPassword123!'
PERMITIDO = 'permitido'      # la operación pasa la autorización (2xx o 400 de validación)
DENEGADO = 'denegado'        # 403
FUERA_DE_ALCANCE = 'oculto'  # 404: el registro no existe para ese perfil


class MatrizPermisosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.del_a = Delegacion.objects.create(nombre='Delegación A', codigo='DLA')
        cls.del_b = Delegacion.objects.create(nombre='Delegación B', codigo='DLB')
        cargo = Cargo.objects.create(nombre='Gestor')
        cls.perfiles = {
            'administrador': Usuario.objects.create_superuser(
                username='m.admin', email='m.admin@example.com', password=PASSWORD,
                rol=Usuario.Rol.ADMINISTRADOR,
            ),
            'supervisor': Usuario.objects.create_user(
                username='m.supervisor', password=PASSWORD, rol=Usuario.Rol.COORDINADOR,
            ),
            'delegado': Usuario.objects.create_user(
                username='m.delegado', password=PASSWORD, rol=Usuario.Rol.DELEGADO,
                delegacion=cls.del_a,
            ),
            'funcionario': Usuario.objects.create_user(
                username='m.funcionario', password=PASSWORD, rol=Usuario.Rol.FUNCIONARIO,
                delegacion=cls.del_a, cargo=cargo,
            ),
        }
        otro = Usuario.objects.create_user(
            username='m.otro', password=PASSWORD, rol=Usuario.Rol.FUNCIONARIO,
            delegacion=cls.del_b, cargo=cargo,
        )
        hoy = timezone.localdate()
        cls.act_propia = Actividad.objects.create(
            funcionario=cls.perfiles['funcionario'], delegacion=cls.del_a, fecha_actividad=hoy,
        )
        cls.act_otra = Actividad.objects.create(funcionario=otro, delegacion=cls.del_b, fecha_actividad=hoy)
        cls.ev_propia = Evidencia.objects.create(actividad=cls.act_propia)
        cls.ev_otra = Evidencia.objects.create(actividad=cls.act_otra)

    def _cliente(self, perfil):
        client = Client()
        client.force_login(self.perfiles[perfil])
        return client

    @staticmethod
    def _resultado(status_code):
        if status_code == 403:
            return DENEGADO
        if status_code == 404:
            return FUERA_DE_ALCANCE
        if status_code < 300 or status_code == 400:
            return PERMITIDO
        return f'HTTP {status_code}'

    def _verificar(self, esperado, peticion):
        for perfil, resultado in esperado.items():
            with self.subTest(perfil=perfil):
                self.assertEqual(self._resultado(peticion(self._cliente(perfil)).status_code), resultado)

    # --- Visibilidad por ámbito ------------------------------------------------

    def test_listado_de_actividades_segun_ambito(self):
        esperado = {
            'administrador': {self.act_propia.pk, self.act_otra.pk},
            'supervisor': {self.act_propia.pk, self.act_otra.pk},
            'delegado': {self.act_propia.pk},          # solo su delegación
            'funcionario': {self.act_propia.pk},       # solo las propias
        }
        for perfil, ids in esperado.items():
            with self.subTest(perfil=perfil):
                datos = self._cliente(perfil).get('/api/actividades/').json()
                self.assertEqual({fila['id'] for fila in datos}, ids)

    def test_detalle_de_compromiso_sin_actividad_de_origen(self):
        """Regresión: un compromiso sin actividad provocaba un error 500 al funcionario."""
        propio = CompromisoAgenda.objects.create(
            solicitante='Junta Vecinal', delegacion=self.del_a,
            responsable=self.perfiles['funcionario'], descripcion='Sin actividad',
        )
        ajeno = CompromisoAgenda.objects.create(
            solicitante='Junta Vecinal', delegacion=self.del_b,
            responsable=self.perfiles['supervisor'], descripcion='Sin actividad',
        )
        self._verificar(
            {'administrador': PERMITIDO, 'supervisor': PERMITIDO,
             'delegado': PERMITIDO, 'funcionario': PERMITIDO},
            lambda c: c.get(f'/api/v1/compromisos/{propio.pk}/'),
        )
        self._verificar(
            {'administrador': PERMITIDO, 'supervisor': PERMITIDO,
             'delegado': FUERA_DE_ALCANCE, 'funcionario': FUERA_DE_ALCANCE},
            lambda c: c.get(f'/api/v1/compromisos/{ajeno.pk}/'),
        )

    def test_detalle_fuera_del_ambito_no_es_accesible(self):
        self._verificar(
            {'administrador': PERMITIDO, 'supervisor': PERMITIDO,
             'delegado': FUERA_DE_ALCANCE, 'funcionario': FUERA_DE_ALCANCE},
            lambda c: c.get(f'/api/actividades/{self.act_otra.pk}/'),
        )

    def test_usuarios_visibles_segun_ambito(self):
        visibles = {
            perfil: {u['id'] for u in self._cliente(perfil).get('/api/usuarios/').json()}
            for perfil in self.perfiles
        }
        self.assertEqual(visibles['funcionario'], {self.perfiles['funcionario'].pk})
        self.assertNotIn(self.act_otra.funcionario_id, visibles['delegado'])
        self.assertIn(self.act_otra.funcionario_id, visibles['supervisor'])

    # --- Operaciones ---------------------------------------------------------------

    def test_modificar_actividades(self):
        self._verificar(
            {'administrador': PERMITIDO, 'supervisor': PERMITIDO,
             'delegado': DENEGADO, 'funcionario': DENEGADO},
            lambda c: c.patch(f'/api/actividades/{self.act_propia.pk}/', {}, content_type='application/json'),
        )

    def test_nadie_puede_eliminar_por_api(self):
        """Normativa: ningún registro se elimina. La API rechaza DELETE (403 o 405) para todos."""
        rutas = {
            'delegaciones': (Delegacion, self.del_a.pk),
            'usuarios': (Usuario, self.perfiles['funcionario'].pk),
            'actividades': (Actividad, self.act_propia.pk),
            'evidencias': (Evidencia, self.ev_propia.pk),
        }
        for recurso, (modelo, pk) in rutas.items():
            for perfil in self.perfiles:
                with self.subTest(recurso=recurso, perfil=perfil):
                    response = self._cliente(perfil).delete(f'/api/{recurso}/{pk}/')
                    self.assertIn(response.status_code, (403, 405))
                    self.assertTrue(modelo.objects.filter(pk=pk).exists())

    def test_anular_compromiso_por_api(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Junta Vecinal', delegacion=self.del_a,
            responsable=self.perfiles['funcionario'], creado_por=self.perfiles['funcionario'],
        )
        url = f'/api/v1/compromisos/{compromiso.pk}/anular/'
        # El Funcionario anula desde la web; la API de compromisos no le permite cambios.
        self.assertEqual(self._cliente('funcionario').post(url, {'motivo': 'x'}).status_code, 403)
        delegado = self._cliente('delegado')
        self.assertEqual(delegado.post(url, {}).status_code, 400)
        self.assertEqual(delegado.post(url, {'motivo': 'Duplicado'}).status_code, 200)
        self.assertTrue(CompromisoAgenda.todos.get(pk=compromiso.pk).anulado)
        self.assertEqual(delegado.get(f'/api/v1/compromisos/{compromiso.pk}/').status_code, 404)

    def test_mantenedores_por_api_son_de_solo_lectura_para_todos(self):
        for recurso in ('delegaciones', 'cargos', 'periodos', 'catalogos', 'items-medicion', 'usuarios'):
            with self.subTest(recurso=recurso):
                self._verificar(
                    {perfil: DENEGADO for perfil in self.perfiles},
                    lambda c: c.post(f'/api/{recurso}/', {}, content_type='application/json'),
                )

    def test_validar_evidencias(self):
        validar = {'decision': 'APROBADA'}
        self._verificar(
            {'administrador': PERMITIDO, 'supervisor': PERMITIDO,
             'delegado': PERMITIDO, 'funcionario': DENEGADO},
            lambda c: c.post(f'/api/evidencias/{self.ev_propia.pk}/validar/', validar,
                             content_type='application/json'),
        )

    def test_delegado_no_valida_evidencias_de_otra_delegacion(self):
        response = self._cliente('delegado').post(
            f'/api/evidencias/{self.ev_otra.pk}/validar/', {'decision': 'APROBADA'},
            content_type='application/json',
        )
        self.assertIn(response.status_code, (403, 404))
        self.ev_otra.refresh_from_db()
        self.assertEqual(self.ev_otra.estado, Evidencia.Estado.PENDIENTE)

    def test_auditoria_solo_para_administracion_y_supervision(self):
        self._verificar(
            {'administrador': PERMITIDO, 'supervisor': PERMITIDO,
             'delegado': DENEGADO, 'funcionario': DENEGADO},
            lambda c: c.get('/api/auditoria/'),
        )

    def test_mantenedores_web_solo_para_administracion(self):
        for perfil in ('delegado', 'funcionario'):
            with self.subTest(perfil=perfil):
                response = self._cliente(perfil).get('/mantenedores/delegaciones/')
                self.assertIn(response.status_code, (302, 403))
                if response.status_code == 302:
                    self.assertNotIn('/mantenedores/', response.url)
        self.assertEqual(self._cliente('administrador').get('/mantenedores/delegaciones/').status_code, 200)

    # --- Segregación de funciones -------------------------------------------------

    def test_nadie_aprueba_su_propia_evidencia(self):
        delegado = self.perfiles['delegado']
        actividad = Actividad.objects.create(
            funcionario=delegado, delegacion=self.del_a, fecha_actividad=timezone.localdate(),
        )
        evidencia = Evidencia.objects.create(actividad=actividad)

        self._cliente('delegado').post(
            f'/api/evidencias/{evidencia.pk}/validar/', {'decision': 'APROBADA'},
            content_type='application/json',
        )

        evidencia.refresh_from_db()
        self.assertNotEqual(evidencia.estado, Evidencia.Estado.APROBADA)

    def test_nadie_aprueba_su_propia_evidencia_desde_la_web(self):
        supervisor = self.perfiles['supervisor']
        actividad = Actividad.objects.create(
            funcionario=supervisor, delegacion=self.del_a, fecha_actividad=timezone.localdate(),
        )
        evidencia = Evidencia.objects.create(actividad=actividad)

        self._cliente('supervisor').post(
            f'/verificacion/{evidencia.pk}/validar/', {'decision': 'APROBADA'},
        )

        evidencia.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.PENDIENTE)
