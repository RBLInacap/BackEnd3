from datetime import date, timedelta
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from sgr_app.forms import AtencionSocialForm
from sgr_app.models import (
    Actividad,
    AjusteIncentivo,
    Area,
    AtencionSocial,
    Cargo,
    CasoSocial,
    CatalogoServicio,
    CompromisoAgenda,
    Delegacion,
    Evidencia,
    Indicador,
    ItemMedicion,
    MetaModel,
    ObservacionCompromiso,
    PeriodoMedicion,
    RegistroAuditoria,
    Solicitante,
    SubatencionSocial,
    TipoActividad,
    TipoAtencionSocial,
    Usuario,
    VersionConfiguracionPeriodo,
)
from sgr_app.serializers import (
    ActividadSerializer,
    AtencionSocialSerializer,
    CasoSocialSerializer,
    UsuarioSerializer,
)
from sgr_app.periodos import get_nombre_trimestre_actual, get_periodo_trimestre_actual
from sgr_app.services.calculos import calcular_dias_periodo, calcular_metricas_funcionario
from sgr_app.validators import (
    normalize_chilean_phone,
    normalize_chilean_rut,
    validate_chilean_rut,
)
from sgr_app.services.evidencias import validar_archivo_evidencia
from sgr_app.web_views import _agrupar_funcionarios_por_cargo


class ActivityWorkflowTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        quarter_month = ((self.today.month - 1) // 3) * 3 + 1
        self.period_start = date(self.today.year, quarter_month, 1)
        next_quarter_month = quarter_month + 3
        if next_quarter_month == 13:
            next_quarter_month = 1
            next_quarter_year = self.today.year + 1
        else:
            next_quarter_year = self.today.year
        next_quarter_start = date(next_quarter_year, next_quarter_month, 1)
        self.period_end = next_quarter_start - timedelta(days=1)

        self.delegacion = Delegacion.objects.create(nombre='Delegación de prueba', codigo='TST')
        self.area = Area.objects.create(nombre='Área de prueba')
        self.tipo_atencion_catalogo = TipoAtencionSocial.objects.create(
            area=self.area,
            nombre='Gestión social',
        )
        self.cargo = Cargo.objects.create(nombre='Gestor de prueba')
        self.funcionario = Usuario.objects.create_user(
            username='funcionario.workflow',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=self.cargo,
            delegacion=self.delegacion,
        )
        self.supervisor = Usuario.objects.create_user(
            username='supervisor.workflow',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            delegacion=self.delegacion,
        )
        self.periodo = PeriodoMedicion.objects.create(
            nombre='Período de prueba',
            fecha_inicio=self.period_start,
            fecha_termino=self.period_end,
        )
        self.item = ItemMedicion.objects.create(
            cargo=self.cargo,
            periodo=self.periodo,
            nombre_item='Actividad de prueba',
            meta_cuantitativa=10,
            ponderador=100,
        )
        self.solicitante = Solicitante.objects.create(
            run='11.111.111-1',
            nombre='Andrea',
            apellido='Prueba Ejemplo',
            apellido_paterno='Prueba',
            apellido_materno='Ejemplo',
            tipo_solicitante='VECINO',
            correo='andrea@example.com',
            telefono='+56 9 1234 5678',
        )
        self.client = Client()
        self.client.force_login(self.funcionario)
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)

    def upload(self, name):
        image_buffer = BytesIO()
        Image.new('RGB', (2, 2), color='blue').save(image_buffer, format='JPEG')
        return SimpleUploadedFile(name, image_buffer.getvalue(), content_type='image/jpeg')

    def profile_form(self, **overrides):
        data = {
            'first_name': 'Andrea',
            'last_name': 'Funcionaria Prueba',
            'email': 'andrea.funcionaria@example.com',
            'email_confirmacion': 'andrea.funcionaria@example.com',
            'telefono': '87654321',
            'password_actual': '',
            'password_nueva': '',
            'password_confirmacion': '',
        }
        data.update(overrides)
        return data

    def test_personal_profile_can_update_contact_details(self):
        response = self.client.post('/mi-ficha/', self.profile_form())

        self.assertRedirects(response, '/mi-ficha/')
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.first_name, 'Andrea')
        self.assertEqual(self.funcionario.last_name, 'Funcionaria Prueba')
        self.assertEqual(self.funcionario.email, 'andrea.funcionaria@example.com')
        self.assertEqual(self.funcionario.telefono, '+56987654321')
        self.assertTrue(self.funcionario.check_password('TestPassword123!'))

    def test_personal_profile_requires_matching_email_confirmation(self):
        response = self.client.post(
            '/mi-ficha/',
            self.profile_form(email_confirmacion='otro@example.com'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Los correos electrónicos no coinciden.')
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.email, '')

    def test_personal_profile_rejects_email_already_used_by_another_account(self):
        Usuario.objects.create_user(
            username='otra.cuenta',
            password='TestPassword123!',
            email='ocupado@example.com',
        )
        response = self.client.post(
            '/mi-ficha/',
            self.profile_form(
                email='ocupado@example.com',
                email_confirmacion='ocupado@example.com',
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Este correo electrónico ya está asociado a otra cuenta.')
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.email, '')

    def test_personal_profile_requires_valid_chilean_phone(self):
        response = self.client.post('/mi-ficha/', self.profile_form(telefono='123'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ingresa 8 dígitos para un teléfono móvil chileno con prefijo +56 9.')
        self.funcionario.refresh_from_db()
        self.assertIsNone(self.funcionario.telefono)

    def test_personal_profile_changes_password_with_current_password_and_keeps_session(self):
        new_password = 'DiferenteSeguro_2026!'
        response = self.client.post(
            '/mi-ficha/',
            self.profile_form(
                password_actual='TestPassword123!',
                password_nueva=new_password,
                password_confirmacion=new_password,
            ),
        )

        self.assertRedirects(response, '/mi-ficha/')
        self.funcionario.refresh_from_db()
        self.assertTrue(self.funcionario.check_password(new_password))
        self.assertEqual(self.client.get('/mi-ficha/').status_code, 200)

    def test_personal_profile_rejects_wrong_current_password_and_mismatched_new_password(self):
        response = self.client.post(
            '/mi-ficha/',
            self.profile_form(
                password_actual='Incorrecta123!',
                password_nueva='DiferenteSeguro_2026!',
                password_confirmacion='OtraDiferenteSeguro_2026!',
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La contraseña actual no es correcta.')
        self.assertContains(response, 'Las nuevas contraseñas no coinciden.')
        self.funcionario.refresh_from_db()
        self.assertTrue(self.funcionario.check_password('TestPassword123!'))

    def test_personal_profile_requires_authentication(self):
        self.client.logout()

        response = self.client.get('/mi-ficha/')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/?next='))

    def test_personal_dashboard_shows_empty_state_when_no_active_employees(self):
        self.funcionario.is_active = False
        self.funcionario.save(update_fields=['is_active'])
        admin = Usuario.objects.create_superuser(
            username='admin.dashboard.empty',
            email='admin@example.com',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        response = self.client.get('/personal/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No hay funcionarios activos')

    def test_anonymous_users_cannot_access_application_pages_or_api(self):
        client = Client()
        protected_urls = (
            '/dashboard/',
            '/semaforo/',
            '/personal/',
            '/verificacion/',
            '/tubo/',
            '/terreno/',
            '/casos-sociales/',
            '/auditoria/',
            '/api/geocodificar/',
            '/api/delegaciones/',
            '/api/usuarios/',
            '/api/metricas/',
            '/api/',
        )

        for url in protected_urls:
            with self.subTest(url=url):
                response = client.get(url)
                if url.startswith('/api/'):
                    self.assertIn(response.status_code, (302, 403))
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.url.startswith('/login/'))

        self.assertEqual(client.get('/login/').status_code, 200)
        self.assertEqual(client.get('/admin/login/').status_code, 404)
        self.assertEqual(
            client.post('/api/auth/login/', {}, content_type='application/json').status_code,
            400,
        )

    def test_two_factor_verification_requires_password_authenticated_pending_session(self):
        response = Client().post('/verificar-2fa/', {
            'user_id': self.funcionario.pk,
            'code': '123456',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_logout_only_accepts_post_and_clears_the_session(self):
        client = Client()
        client.force_login(self.funcionario)

        get_response = client.get('/logout/')
        self.assertEqual(get_response.status_code, 405)
        self.assertTrue(client.session.get('_auth_user_id'))

        post_response = client.post('/logout/')
        self.assertEqual(post_response.status_code, 302)
        self.assertNotIn('_auth_user_id', client.session)

    @patch('sgr_app.web_views.time.time', return_value=59)
    def test_two_factor_uses_password_pending_session_and_account_totp(self, _mock_time):
        self.funcionario.requires_2fa = True
        self.funcionario.totp_secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        self.funcionario.save(update_fields=['requires_2fa', 'totp_secret'])
        client = Client()

        password_response = client.post('/login/', {
            'username': self.funcionario.username,
            'password': 'TestPassword123!',
        })
        self.assertEqual(password_response.status_code, 200)
        self.assertTrue(password_response.context['show_2fa'])
        self.assertFalse(password_response.wsgi_request.user.is_authenticated)

        two_factor_response = client.post('/verificar-2fa/', {
            'user_id': '999999',
            'code': '287082',
        })

        self.assertEqual(two_factor_response.status_code, 302)
        dashboard_response = client.get('/personal/')
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(dashboard_response.context['funcionario'].pk, self.funcionario.pk)

    def test_demo_passwords_do_not_authenticate_users(self):
        client = Client()

        response = client.post('/login/', {
            'username': self.funcionario.username,
            'password': 'AdminSGR2026!',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_functionary_cannot_mutate_user_api_or_other_users_commitments(self):
        client = Client()
        client.force_login(self.funcionario)

        response = client.post('/api/usuarios/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 403)

        other = Usuario.objects.create_user(
            username='otro.funcionario',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Vecino de prueba',
            delegacion=self.delegacion,
            responsable=other,
            creado_por=other,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            descripcion='Compromiso de otra persona',
        )
        client.raise_request_exception = False
        response = client.post(f'/tubo/{compromiso.pk}/eliminar/')

        self.assertEqual(response.status_code, 403)
        self.assertTrue(CompromisoAgenda.objects.filter(pk=compromiso.pk).exists())

    def test_authenticated_api_mutations_require_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.funcionario)

        response = client.post('/api/usuarios/', {}, content_type='application/json')

        self.assertEqual(response.status_code, 403)

    def activity_form(self, **overrides):
        data = {
            'funcionario_id': str(self.funcionario.id),
            'fecha_actividad': self.today.isoformat(),
            'item_medicion_id': str(self.item.id),
            'solicitante_id': str(self.solicitante.id),
            'contacto_telefono': '12345678',
            'direccion': 'Calle de prueba 123',
            'solicitud_problema': 'Luminaria apagada',
            'accion_ejecutada': 'Se inspeccionó el poste',
            'compromiso_fecha': self.today.isoformat(),
            'compromiso_territorio': 'Sector Centro',
            'compromiso_area_apoyo': 'Operaciones y Territorio',
            'compromiso_descripcion': 'Dar seguimiento a la solicitud',
        }
        data.update(overrides)
        return data

    def test_general_dashboard_displays_calendar_quarter_for_today(self):
        coordinator = Usuario.objects.create_user(
            username='coordinador.dashboard.periodo',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(coordinator)

        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['periodo'].id, self.periodo.id)
        self.assertEqual(
            response.context['periodo_nombre_actual'],
            get_nombre_trimestre_actual(self.today),
        )
        self.assertEqual(
            response.context['nombre_periodo_activo'],
            get_nombre_trimestre_actual(self.today),
        )
        self.assertContains(response, get_nombre_trimestre_actual(self.today))

    def test_role_dashboards_aggregate_employee_results_by_cargo_and_allow_drilldown(self):
        colega = Usuario.objects.create_user(
            username='funcionario.cargo.companero',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=self.cargo,
            delegacion=self.delegacion,
        )
        otra_delegacion = Delegacion.objects.create(
            nombre='Delegación para comparación',
            codigo='CMP',
        )
        otro_cargo = Cargo.objects.create(nombre='Analista de apoyo')
        funcionario_otra_delegacion = Usuario.objects.create_user(
            username='funcionario.cargo.otra.delegacion',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=otro_cargo,
            delegacion=otra_delegacion,
        )
        for funcionario, cantidad in ((self.funcionario, 1), (colega, 2)):
            for _ in range(cantidad):
                actividad = Actividad.objects.create(
                    funcionario=funcionario,
                    delegacion=self.delegacion,
                    item_medicion=self.item,
                    fecha_actividad=self.today,
                )
                Evidencia.objects.create(
                    actividad=actividad,
                    estado=Evidencia.Estado.APROBADA,
                )

        delegado = Usuario.objects.create_user(
            username='delegado.cargo.dashboard',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            es_delegado=True,
            delegacion=self.delegacion,
        )
        self.client.force_login(delegado)
        delegado_response = self.client.get('/dashboard/')

        self.assertEqual(delegado_response.status_code, 200)
        resumen_delegacion = delegado_response.context['comunal']['delegaciones'][0]
        self.assertEqual(resumen_delegacion['total_funcionarios'], 2)
        self.assertEqual(resumen_delegacion['promedio_ponderado_pct'], 15.0)
        self.assertEqual(len(delegado_response.context['resumenes_por_cargo']), 1)
        resumen_cargo_delegado = delegado_response.context['resumenes_por_cargo'][0]
        self.assertEqual(resumen_cargo_delegado['cargo'], self.cargo.nombre)
        self.assertEqual(resumen_cargo_delegado['total_funcionarios'], 2)
        self.assertEqual(resumen_cargo_delegado['promedio_ponderado_pct'], 15.0)
        self.assertEqual(
            {funcionario['funcionario_id'] for funcionario in resumen_cargo_delegado['funcionarios']},
            {self.funcionario.pk, colega.pk},
        )
        self.assertNotContains(delegado_response, funcionario_otra_delegacion.username)
        self.assertContains(delegado_response, colega.username)
        self.assertIn(f'id={colega.pk}'.encode(), delegado_response.content)

        administrator = Usuario.objects.create_user(
            username='admin.cargo.dashboard',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(administrator)
        admin_response = self.client.get('/dashboard/')

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(
            {group['cargo_id'] for group in admin_response.context['resumenes_por_cargo']},
            {self.cargo.pk, otro_cargo.pk},
        )
        self.assertEqual(
            sum(group['total_funcionarios'] for group in admin_response.context['resumenes_por_cargo']),
            3,
        )
        self.assertContains(admin_response, 'Desempeño global por cargo')
        self.assertContains(admin_response, funcionario_otra_delegacion.username)

    def test_semaforo_uses_current_quarter_and_admin_thresholds(self):
        self.client.force_login(self.supervisor)
        self.periodo.umbral_semaforo_verde = 110.0
        self.periodo.umbral_semaforo_ambar = 45.0
        self.periodo.save()

        response = self.client.get('/semaforo/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['periodo_configurado'])
        self.assertEqual(response.context['periodo'].pk, self.periodo.pk)
        self.assertEqual(response.context['umbral_verde'], 110.0)
        self.assertEqual(response.context['umbral_ambar'], 45.0)
        info = response.context['comunal']['info_tiempo']
        self.assertEqual(info['dias_transcurridos'], (self.today - self.period_start).days + 1)
        self.assertEqual(info['dias_totales'], (self.period_end - self.period_start).days + 1)
        self.assertContains(response, 'Días corridos del trimestre.')
        self.assertContains(response, 'del objetivo (Verde)')
        self.assertContains(response, 'del objetivo (Ámbar)')

    def test_unconfigured_quarter_uses_dynamic_dates_and_default_thresholds(self):
        self.client.force_login(self.supervisor)
        self.periodo.fecha_inicio = date(self.today.year - 1, 7, 1)
        self.periodo.fecha_termino = date(self.today.year - 1, 9, 30)
        self.periodo.save()

        response = self.client.get('/semaforo/')
        periodo_actual, configurado = get_periodo_trimestre_actual(self.today)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(configurado)
        self.assertFalse(response.context['periodo_configurado'])
        self.assertEqual(response.context['periodo'].fecha_inicio, periodo_actual.fecha_inicio)
        self.assertEqual(response.context['periodo'].fecha_termino, periodo_actual.fecha_termino)
        self.assertEqual(response.context['umbral_verde'], 100.0)
        self.assertEqual(response.context['umbral_ambar'], 60.0)
        self.assertContains(response, 'se aplican los umbrales iniciales')
        self.assertNotContains(response, 'Cuarto Trimestre - 2026')

    def test_quarter_label_uses_current_month_and_year(self):
        self.assertEqual(
            get_nombre_trimestre_actual(date(2026, 10, 21)),
            '4° Trimestre (Octubre - Diciembre) - 2026',
        )
        self.assertEqual(
            get_nombre_trimestre_actual(date(2027, 1, 1)),
            '1° Trimestre (Enero - Marzo) - 2027',
        )

    def test_personal_dashboard_displays_calendar_quarter_label(self):
        response = self.client.get('/personal/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Período: {get_nombre_trimestre_actual(self.today)}')
        self.assertNotContains(response, f'Período: {self.periodo.nombre}')

    def test_personal_dashboard_uses_calendar_quarter_when_previous_period_is_active(self):
        self.periodo.fecha_inicio = date(self.today.year - 1, 7, 1)
        self.periodo.fecha_termino = date(self.today.year - 1, 9, 30)
        self.periodo.save()

        response = self.client.get('/personal/')
        periodo_actual, periodo_configurado = get_periodo_trimestre_actual(self.today)
        dias_transcurridos = (self.today - periodo_actual.fecha_inicio).days + 1
        dias_totales = (periodo_actual.fecha_termino - periodo_actual.fecha_inicio).days + 1

        self.assertEqual(response.status_code, 200)
        self.assertFalse(periodo_configurado)
        self.assertFalse(response.context['periodo_configurado'])
        self.assertEqual(response.context['metricas']['dias_transcurridos'], dias_transcurridos)
        self.assertEqual(response.context['metricas']['dias_totales'], dias_totales)
        self.assertContains(response, f'{dias_transcurridos} / {dias_totales}')
        self.assertContains(response, 'Trimestre actual sin configurar.')
        self.assertContains(response, 'Matriz de Cumplimiento por Ítems Medibles')
        self.assertContains(
            response,
            'La ficha no tiene datos ponderables para el período seleccionado.',
        )
        self.assertNotContains(response, 'Cumplimiento Ponderado Personal')

    def test_personal_dashboard_shows_empty_matrix_without_items(self):
        periodo_sin_items = PeriodoMedicion.objects.create(
            nombre='Período actual sin ítems',
            fecha_inicio=self.period_start,
            fecha_termino=self.period_end,
        )

        response = self.client.get(
            '/personal/',
            {'periodo': periodo_sin_items.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['periodo_configurado'])
        self.assertFalse(response.context['metas_configuradas'])
        self.assertContains(response, 'Matriz de Cumplimiento por Ítems Medibles')
        self.assertContains(
            response,
            'No hay ítems de medición activos para el cargo',
        )
        self.assertContains(
            response,
            'La ficha no tiene datos ponderables para el período seleccionado.',
        )
        self.assertNotContains(response, 'Resultado Ponderado Personal')

    def test_personal_dashboard_filters_historic_period_and_item_sources(self):
        periodo_anterior_fin = self.period_start - timedelta(days=1)
        periodo_anterior_inicio = date(
            periodo_anterior_fin.year,
            ((periodo_anterior_fin.month - 1) // 3) * 3 + 1,
            1,
        )
        self.periodo.fecha_inicio = periodo_anterior_inicio
        self.periodo.fecha_termino = periodo_anterior_fin
        self.periodo.save()
        self.item.meta_cuantitativa = 2
        self.item.save()

        actividad_aprobada = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=periodo_anterior_inicio,
            actividad_solicitud='Atención histórica aprobada',
        )
        Evidencia.objects.create(
            actividad=actividad_aprobada,
            estado=Evidencia.Estado.APROBADA,
        )
        actividad_pendiente = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=periodo_anterior_fin,
            actividad_solicitud='Atención histórica pendiente',
        )
        Evidencia.objects.create(
            actividad=actividad_pendiente,
            estado=Evidencia.Estado.PENDIENTE,
        )
        Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Atención fuera del período',
        )
        CompromisoAgenda.objects.create(
            solicitante='Vecino período anterior',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=periodo_anterior_inicio,
            fecha_comprometida=periodo_anterior_inicio,
            descripcion='Compromiso histórico',
        )
        CompromisoAgenda.objects.create(
            solicitante='Vecino período actual',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            descripcion='Compromiso fuera del período',
        )

        response = self.client.get(
            '/personal/',
            {'periodo': self.periodo.pk, 'item': self.item.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['periodo'].pk, self.periodo.pk)
        self.assertEqual(response.context['metricas']['total_actividades'], 2)
        self.assertEqual(response.context['metricas']['items'][0]['avance_aprobado'], 1)
        self.assertEqual(response.context['metricas']['items'][0]['cumplimiento_pct'], 50.0)
        self.assertEqual(response.context['metricas']['compromisos']['total'], 1)
        self.assertEqual(
            {actividad.pk for actividad in response.context['actividades']},
            {actividad_aprobada.pk, actividad_pendiente.pk},
        )
        self.assertContains(response, 'Ver actividades fuente (1)')
        self.assertContains(response, 'actividades con evidencia aprobada / meta del ítem')
        self.assertContains(response, 'Atención histórica aprobada')
        self.assertContains(response, 'Atención histórica pendiente')
        self.assertNotContains(response, 'Atención fuera del período')

    def test_personal_dashboard_paginates_all_item_source_activities(self):
        for index in range(51):
            actividad = Actividad.objects.create(
                funcionario=self.funcionario,
                delegacion=self.delegacion,
                item_medicion=self.item,
                fecha_actividad=self.today,
                actividad_solicitud=f'Actividad fuente {index}',
            )
            Evidencia.objects.create(
                actividad=actividad,
                estado=Evidencia.Estado.APROBADA,
            )

        params = {'periodo': self.periodo.pk, 'item': self.item.pk}
        primera_pagina = self.client.get('/personal/', params)
        segunda_pagina = self.client.get('/personal/', {**params, 'pagina': '2'})

        self.assertEqual(len(primera_pagina.context['actividades']), 50)
        self.assertEqual(primera_pagina.context['metricas']['items'][0]['avance_aprobado'], 51)
        self.assertContains(primera_pagina, 'Página 1 de 2')
        self.assertEqual(len(segunda_pagina.context['actividades']), 1)
        self.assertContains(segunda_pagina, 'Página 2 de 2')
        self.assertIn(f'item={self.item.pk}'.encode(), segunda_pagina.content)
        self.assertIn(b'pagina=1', segunda_pagina.content)

    def test_approved_evidence_closes_activity_for_additional_evidence(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Actividad cerrada por aprobación',
        )
        Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.APROBADA,
        )

        dashboard_response = self.client.get('/personal/')
        self.assertEqual(dashboard_response.status_code, 200)
        actividad_en_contexto = next(
            entry
            for entry in dashboard_response.context['actividades']
            if entry.pk == actividad.pk
        )
        self.assertTrue(actividad_en_contexto.tiene_evidencia_aprobada)
        self.assertContains(dashboard_response, 'Cerrada')
        self.assertNotContains(
            dashboard_response,
            f'data-actividad-id="{actividad.pk}"',
            html=False,
        )

        web_response = self.client.post(
            f'/personal/actividades/{actividad.pk}/evidencias/',
        )
        self.assertEqual(web_response.status_code, 302)
        self.assertEqual(actividad.evidencias.count(), 1)

        administrator = Usuario.objects.create_user(
            username='admin.evidence.lock',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(administrator)
        api_response = self.client.post(
            '/api/evidencias/',
            data=json.dumps({'actividad': actividad.pk}),
            content_type='application/json',
        )
        self.assertEqual(api_response.status_code, 400)
        self.assertIn('cerrada', api_response.json()['actividad'][0].lower())
        self.assertEqual(actividad.evidencias.count(), 1)

    def test_compliance_calculation_applies_approved_adjustments_and_preserves_base(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.APROBADA,
        )
        administrator = Usuario.objects.create_user(
            username='admin.aprobar.ajuste',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        incentivo = AjusteIncentivo.objects.create(
            funcionario=self.funcionario,
            registrado_por=administrator,
            periodo=self.periodo,
            tipo='INCENTIVO',
            motivo='Reconocimiento por cumplimiento',
            valor_porcentaje=5,
            estado='PENDIENTE',
        )
        penalizacion = AjusteIncentivo.objects.create(
            funcionario=self.funcionario,
            registrado_por=administrator,
            periodo=self.periodo,
            tipo='PENALIZACION',
            motivo='Incumplimiento justificado',
            valor_porcentaje=2,
            estado='PENDIENTE',
        )
        incentivo.aprobarAjuste(administrator)
        penalizacion.aprobarAjuste(administrator)

        metricas = calcular_metricas_funcionario(self.funcionario, self.periodo, self.today)

        self.assertEqual(metricas['items'][0]['cumplimiento_pct'], 10.0)
        self.assertEqual(metricas['total_ponderado_pct'], 10.0)
        self.assertEqual(sum(item['aporte_ponderado'] for item in metricas['items']), 10.0)
        self.assertEqual(metricas['total_ajustado_pct'], 13.0)
        self.assertEqual(
            [ajuste['efecto_pct'] for ajuste in metricas['ajustes']],
            [5.0, -2.0],
        )
        self.assertEqual(metricas['ajustes'][0]['responsable'], administrator.username)

    def test_incentive_adjustment_workflow_audits_creation_and_approval(self):
        administrator = Usuario.objects.create_user(
            username='admin.registro.ajuste',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(administrator)
        manager_page = self.client.get('/ajustes/', {'periodo': self.periodo.pk})
        self.assertEqual(manager_page.status_code, 200)
        self.assertContains(manager_page, 'Registrar ajuste')

        response = self.client.post('/ajustes/', {
            'periodo': str(self.periodo.pk),
            'funcionario': str(self.funcionario.pk),
            'tipo': 'BONIFICACION',
            'motivo': 'Resultado destacado',
            'valor_porcentaje': '7.5',
        })

        self.assertRedirects(response, f'/ajustes/?periodo={self.periodo.pk}')
        ajuste = AjusteIncentivo.objects.get(funcionario=self.funcionario)
        self.assertEqual(ajuste.estado, 'PENDIENTE')
        self.assertEqual(ajuste.registrado_por, administrator)
        registro_creacion = RegistroAuditoria.objects.get(
            entidad='AjusteIncentivo',
            identificador_registro=str(ajuste.pk),
            accion='CREAR_AJUSTE_INCENTIVO',
        )
        self.assertEqual(registro_creacion.valor_nuevo['motivo'], 'Resultado destacado')
        self.assertEqual(registro_creacion.valor_nuevo['registrado_por_id'], administrator.pk)

        response = self.client.post(
            f'/ajustes/{ajuste.pk}/decidir/',
            {'decision': 'APROBAR'},
        )

        self.assertRedirects(response, f'/ajustes/?periodo={self.periodo.pk}')
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.estado, 'APROBADO')
        self.assertEqual(ajuste.aprobadoPor, administrator)
        self.assertIsNotNone(ajuste.fechaAprobacion)
        manager_page = self.client.get('/ajustes/', {'periodo': self.periodo.pk})
        self.assertContains(manager_page, 'Resultado destacado')
        self.assertEqual(manager_page.context['ajustes'][0].efecto_pct, 7.5)
        self.assertContains(manager_page, 'Efecto:')
        self.assertContains(manager_page, 'Resultado:')
        self.assertContains(manager_page, administrator.username)
        auditoria_aprobacion = RegistroAuditoria.objects.get(
            entidad='AjusteIncentivo',
            identificador_registro=str(ajuste.pk),
            accion='APROBAR_AJUSTE_INCENTIVO',
        )
        self.assertEqual(auditoria_aprobacion.valor_anterior['estado'], 'PENDIENTE')
        self.assertEqual(auditoria_aprobacion.valor_nuevo['estado'], 'APROBADO')

        metricas = calcular_metricas_funcionario(self.funcionario, self.periodo, self.today)
        self.assertEqual(metricas['total_ajustado_pct'], 7.5)
        personal_response = self.client.get('/personal/', {
            'id': self.funcionario.pk,
            'periodo': self.periodo.pk,
        })
        self.assertEqual(personal_response.status_code, 200)
        self.assertEqual(
            personal_response.context['metricas']['total_ajustado_pct'],
            7.5,
        )
        self.assertContains(personal_response, 'Resultado Ponderado Personal')

    def test_incentive_adjustment_rejects_missing_reason_and_regular_user(self):
        self.client.force_login(self.supervisor)
        forbidden = Usuario.objects.create_user(
            username='funcionario.sin.permiso.ajustes',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        regular_client = Client()
        regular_client.force_login(forbidden)

        self.assertEqual(regular_client.get('/ajustes/').status_code, 403)
        response = self.client.post('/ajustes/', {
            'periodo': str(self.periodo.pk),
            'funcionario': str(self.funcionario.pk),
            'tipo': 'PENALIZACION',
            'motivo': '   ',
            'valor_porcentaje': '4',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El motivo del ajuste es obligatorio.')
        self.assertFalse(AjusteIncentivo.objects.exists())

    def test_expected_daily_goal_never_exceeds_one_hundred_percent(self):
        after_period = self.period_end + timedelta(days=1)

        metrics = calcular_dias_periodo(self.periodo, after_period)

        self.assertEqual(metrics['meta_esperada_pct'], 100.0)
        self.assertLessEqual(metrics['meta_esperada_pct'], 100.0)

    def test_activity_registration_saves_requester_and_multiple_evidence(self):
        servicio = CatalogoServicio.objects.create(
            codigo='ACT-01',
            nombre='Atención territorial',
            area='Operaciones',
        )
        data = self.activity_form(
            latitud='-29.9',
            longitud='-71.2',
            catalogo_servicio_id=str(servicio.pk),
        )
        data['fotos'] = [self.upload('foto-1.jpg'), self.upload('foto-2.jpg')]
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post(
                '/actividad-diaria/registrar-actividad/',
                data,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f'/personal/?id={self.funcionario.pk}')
        confirmation = self.client.get(response.url)
        actividad = Actividad.objects.get(funcionario=self.funcionario)
        self.assertContains(
            confirmation,
            f'Actividad #{actividad.pk} registrada correctamente.',
        )
        solicitante = self.solicitante
        self.assertEqual(actividad.solicitante_id, solicitante.id)
        self.assertEqual(actividad.contacto_telefono, '+56912345678')
        self.assertEqual(actividad.evidencias.count(), 2)
        self.assertEqual(actividad.latitud, -29.9)
        self.assertEqual(actividad.longitud, -71.2)
        self.assertEqual(actividad.catalogo_servicio_id, servicio.pk)
        self.assertEqual(actividad.catalogo_servicio_nombre, 'Atención territorial')
        self.assertEqual(actividad.catalogo_servicio_area, 'Operaciones')
        compromiso = CompromisoAgenda.objects.get(actividad=actividad)
        self.assertEqual(actividad.compromiso_agenda_id, compromiso.id)
        self.assertEqual(compromiso.fecha_compromiso, self.today)
        self.assertEqual(compromiso.territorio, 'Sector Centro')
        self.assertEqual(compromiso.descripcion, 'Dar seguimiento a la solicitud')
        audit = RegistroAuditoria.objects.get(
            entidad='Actividad',
            identificador_registro=str(actividad.pk),
            accion='REGISTRO_TODO_EN_UNO',
        )
        self.assertEqual(audit.valor_nuevo['compromiso_id'], compromiso.pk)
        self.assertEqual(len(audit.valor_nuevo['evidencias']), 2)

    def test_activity_registration_allows_no_evidence_or_commitment(self):
        data = self.activity_form()
        for field in (
            'compromiso_fecha',
            'compromiso_territorio',
            'compromiso_area_apoyo',
            'compromiso_descripcion',
        ):
            data.pop(field)

        response = self.client.post('/actividad-diaria/registrar-actividad/', data)

        self.assertEqual(response.status_code, 302)
        actividad = Actividad.objects.get(funcionario=self.funcionario)
        self.assertFalse(actividad.evidencias.exists())
        self.assertIsNone(actividad.compromiso_agenda_id)
        audit = RegistroAuditoria.objects.get(
            entidad='Actividad',
            identificador_registro=str(actividad.pk),
            accion='REGISTRO_TODO_EN_UNO',
        )
        self.assertEqual(audit.valor_nuevo['evidencias'], [])
        self.assertIsNone(audit.valor_nuevo['compromiso_id'])

    def test_activity_upload_records_real_format_size_and_uploader(self):
        data = self.activity_form()
        data['fotos'] = [self.upload('foto-original.jpg')]

        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post('/actividad-diaria/registrar-actividad/', data)

        self.assertEqual(response.status_code, 302)
        evidencia = Evidencia.objects.get()
        self.assertEqual(evidencia.subido_por, self.funcionario)
        self.assertEqual(evidencia.metadatos['nombre_archivo'], 'foto-original.jpg')
        self.assertEqual(evidencia.metadatos['formato'], 'JPEG')
        self.assertEqual(evidencia.metadatos['mime_type'], 'image/jpeg')
        self.assertGreater(evidencia.metadatos['tamano_bytes'], 0)
        self.assertTrue(evidencia.archivo_foto.name.endswith('.jpg'))

    def test_evidence_upload_rejects_mime_spoofing_and_oversized_files(self):
        archivo_falso = SimpleUploadedFile(
            'no-es-imagen.jpg',
            b'contenido que no es una imagen',
            content_type='image/jpeg',
        )
        with self.assertRaises(ValidationError):
            validar_archivo_evidencia(archivo_falso)

        with override_settings(EVIDENCE_MAX_FILE_SIZE=1):
            with self.assertRaises(ValidationError):
                validar_archivo_evidencia(self.upload('grande.jpg'))

    def test_activity_api_rejects_spoofed_images_before_creating_activity(self):
        response = self.client.post('/api/actividades/', {
            'funcionario': self.funcionario.pk,
            'delegacion': self.delegacion.pk,
            'item_medicion': self.item.pk,
            'fecha_actividad': self.today.isoformat(),
            'actividad_solicitud': 'Actividad API de prueba',
            'accion_ejecutada': 'Acción de prueba',
            'direccion': 'La Serena',
            'contacto_telefono': '12345678',
            'fotos': SimpleUploadedFile(
                'foto-falsa.jpg',
                b'no es una imagen',
                content_type='image/jpeg',
            ),
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn('fotos', response.json())
        self.assertFalse(Actividad.objects.exists())

    def test_activity_api_validates_activity_date_and_measurement_item(self):
        payload = {
            'funcionario': self.funcionario.pk,
            'delegacion': self.delegacion.pk,
            'fecha_actividad': (self.today + timedelta(days=1)).isoformat(),
            'contacto_telefono': '12345678',
        }
        response = self.client.post(
            '/api/actividades/',
            payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('fecha_actividad', response.json())
        self.assertFalse(Actividad.objects.exists())

        payload['fecha_actividad'] = self.today.isoformat()
        payload.pop('item_medicion', None)
        response = self.client.post(
            '/api/actividades/',
            payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('item_medicion', response.json())
        self.assertFalse(Actividad.objects.exists())

        payload['item_medicion'] = self.item.pk
        payload['fecha_actividad'] = (
            self.period_start - timedelta(days=1)
        ).isoformat()
        response = self.client.post(
            '/api/actividades/',
            payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('item_medicion', response.json())
        self.assertFalse(Actividad.objects.exists())

        payload['fecha_actividad'] = self.today.isoformat()
        other_cargo = Cargo.objects.create(nombre='Cargo API incompatible')
        other_item = ItemMedicion.objects.create(
            cargo=other_cargo,
            periodo=self.periodo,
            nombre_item='Ítem API incompatible',
            meta_cuantitativa=1,
            ponderador=100,
        )
        payload['item_medicion'] = other_item.pk
        response = self.client.post(
            '/api/actividades/',
            payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('item_medicion', response.json())
        self.assertFalse(Actividad.objects.exists())

    def test_activity_api_records_uploader_and_metadata_for_uploaded_image(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post('/api/actividades/', {
                'funcionario': self.funcionario.pk,
                'delegacion': self.delegacion.pk,
                'item_medicion': self.item.pk,
                'fecha_actividad': self.today.isoformat(),
                'actividad_solicitud': 'Actividad API de prueba',
                'accion_ejecutada': 'Acción de prueba',
                'direccion': 'La Serena',
                'contacto_telefono': '12345678',
                'fotos': self.upload('foto-api.jpg'),
            })

        self.assertEqual(response.status_code, 201, response.content)
        evidencia = Evidencia.objects.get()
        self.assertEqual(evidencia.subido_por, self.funcionario)
        self.assertEqual(evidencia.metadatos['formato'], 'JPEG')
        self.assertEqual(evidencia.metadatos['nombre_archivo'], 'foto-api.jpg')

    def test_activity_api_annulment_recalculates_metrics_and_keeps_audit(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Actividad aprobada para anulación',
        )
        Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.APROBADA,
        )
        metricas_antes = calcular_metricas_funcionario(
            self.funcionario,
            self.periodo,
        )
        item_antes = next(
            item for item in metricas_antes['items']
            if item['item_id'] == self.item.pk
        )
        self.assertEqual(item_antes['avance_aprobado'], 1)

        api_client = APIClient()
        api_client.force_authenticate(user=self.supervisor)
        url = f'/api/actividades/{actividad.pk}/anular/'
        response = api_client.post(url, {'motivo': 'Registro duplicado'}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        actividad.refresh_from_db()
        self.assertTrue(actividad.anulada)
        self.assertEqual(actividad.motivo_anulacion, 'Registro duplicado')
        self.assertTrue(RegistroAuditoria.objects.filter(
            accion='ANULAR_ACTIVIDAD',
            identificador_registro=str(actividad.pk),
        ).exists())
        metricas_despues = calcular_metricas_funcionario(
            self.funcionario,
            self.periodo,
        )
        item_despues = next(
            item for item in metricas_despues['items']
            if item['item_id'] == self.item.pk
        )
        self.assertEqual(item_despues['avance_aprobado'], 0)

    def test_expired_evidence_retention_removes_file_but_keeps_metadata(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        with override_settings(
            MEDIA_ROOT=self.media_dir.name,
            EVIDENCE_RETENTION_DAYS=30,
        ):
            evidencia = Evidencia.objects.create(
                actividad=actividad,
                subido_por=self.funcionario,
                metadatos={'formato': 'JPEG', 'tamano_bytes': 123},
            )
            evidencia.archivo_foto.save('vencida.jpg', self.upload('foto.jpg'), save=True)
            ruta_archivo = evidencia.archivo_foto.name
            Evidencia.objects.filter(pk=evidencia.pk).update(
                fecha_subida=timezone.now() - timedelta(days=31)
            )

            call_command('purgar_evidencias_vencidas', stdout=StringIO())

            evidencia.refresh_from_db()
            self.assertFalse(evidencia.archivo_foto)
            self.assertIsNotNone(evidencia.archivos_eliminados_en)
            self.assertEqual(
                evidencia.metadatos,
                {'formato': 'JPEG', 'tamano_bytes': 123},
            )
            self.assertFalse(evidencia.archivo_foto.storage.exists(ruta_archivo))

    def test_deleting_evidence_record_deletes_its_uploaded_file(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            evidencia = Evidencia.objects.create(actividad=actividad)
            evidencia.archivo_foto.save('eliminar.jpg', self.upload('foto.jpg'), save=True)
            storage = evidencia.archivo_foto.storage
            ruta_archivo = evidencia.archivo_foto.name
            self.assertTrue(storage.exists(ruta_archivo))

            evidencia.delete()

            self.assertFalse(storage.exists(ruta_archivo))

    def test_activity_registration_rolls_back_all_related_records_if_audit_fails(self):
        data = self.activity_form()
        data['fotos'] = [self.upload('foto-rollback.jpg')]

        with override_settings(MEDIA_ROOT=self.media_dir.name):
            with patch(
                'sgr_app.web_views.RegistroAuditoria.objects.create',
                side_effect=IntegrityError('audit insert failed'),
            ):
                with self.assertRaises(IntegrityError):
                    self.client.post('/actividad-diaria/registrar-actividad/', data)

        self.assertFalse(Actividad.objects.exists())
        self.assertFalse(Evidencia.objects.exists())
        self.assertFalse(CompromisoAgenda.objects.exists())

    def test_activity_registration_uses_calendar_quarter_when_previous_period_is_open(self):
        previous_period_end = self.period_start - timedelta(days=1)
        previous_period_start = date(
            previous_period_end.year,
            ((previous_period_end.month - 1) // 3) * 3 + 1,
            1,
        )
        self.periodo.fecha_inicio = previous_period_start
        self.periodo.fecha_termino = previous_period_end
        self.periodo.save()

        response = self.client.get('/actividad-diaria/registrar-actividad/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['periodo'].fecha_inicio, self.period_start)
        self.assertEqual(response.context['periodo'].fecha_termino, self.period_end)
        self.assertContains(response, f'min="{self.period_start.isoformat()}"')
        self.assertContains(response, f'max="{self.period_end.isoformat()}"')

        data = self.activity_form(item_medicion_id='')
        response = self.client.post('/actividad-diaria/registrar-actividad/', data)

        self.assertEqual(response.status_code, 302)
        compromiso = CompromisoAgenda.objects.get(actividad__funcionario=self.funcionario)
        self.assertEqual(compromiso.fecha_compromiso, self.today)

    def test_activity_registration_requires_commitment_details(self):
        data = self.activity_form(compromiso_descripcion='')
        response = self.client.post('/actividad-diaria/registrar-actividad/', data)

        self.assertIn('activity_error=compromiso_datos', response.url)
        self.assertFalse(Actividad.objects.exists())
        self.assertFalse(CompromisoAgenda.objects.exists())

    def test_activity_registration_rejects_invalid_location_coordinates(self):
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(latitud='91', longitud='-71.2'),
        )

        self.assertIn('activity_error=ubicacion_invalida', response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_invalid_dates_do_not_create_activity(self):
        future = self.today + timedelta(days=1)
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(
                fecha_actividad=future.isoformat(),
                solicitante_id=str(self.solicitante.id),
            ),
        )
        self.assertIn('activity_error=fecha_futura', response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_activity_date_must_belong_to_selected_items_period(self):
        outside_period = self.period_start - timedelta(days=1)
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(fecha_actividad=outside_period.isoformat()),
        )

        self.assertIn('activity_error=fecha_periodo', response.url)
        self.assertFalse(Actividad.objects.exists())
        error_page = self.client.get(response.url)
        self.assertContains(error_page, 'Corrige los siguientes campos')
        self.assertContains(error_page, 'Fecha de actividad:')
        self.assertContains(
            error_page,
            'La fecha de actividad debe pertenecer al período del ítem seleccionado.',
        )

    def test_activity_registration_rejects_missing_measurement_when_items_exist(self):
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(item_medicion_id=''),
        )

        self.assertIn('activity_error=item_invalido', response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_activity_measurement_choices_are_scoped_to_cargo_period_and_active_items(self):
        inactivo = ItemMedicion.objects.create(
            cargo=self.cargo,
            periodo=self.periodo,
            nombre_item='Ítem inactivo',
            meta_cuantitativa=1,
            ponderador=100,
            activo=False,
        )
        otro_cargo = Cargo.objects.create(nombre='Cargo no relacionado')
        item_otro_cargo = ItemMedicion.objects.create(
            cargo=otro_cargo,
            periodo=self.periodo,
            nombre_item='Ítem de otro cargo',
            meta_cuantitativa=1,
            ponderador=100,
        )
        periodo_anterior = PeriodoMedicion.objects.create(
            nombre='Período anterior para ítems',
            fecha_inicio=self.period_start - timedelta(days=90),
            fecha_termino=self.period_start - timedelta(days=1),
        )
        item_otro_periodo = ItemMedicion.objects.create(
            cargo=self.cargo,
            periodo=periodo_anterior,
            nombre_item='Ítem de otro período',
            meta_cuantitativa=1,
            ponderador=100,
        )

        response = self.client.get('/actividad-diaria/registrar-actividad/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.item.nombre_item)
        self.assertContains(response, 'data-items-url="/actividad-diaria/items/"')
        self.assertNotContains(response, inactivo.nombre_item)
        self.assertNotContains(response, item_otro_cargo.nombre_item)
        self.assertNotContains(response, item_otro_periodo.nombre_item)

        items_response = self.client.get(
            '/actividad-diaria/items/',
            {'funcionario_id': self.funcionario.pk},
        )
        self.assertEqual(items_response.status_code, 200)
        self.assertEqual(
            [item['id'] for item in items_response.json()['items']],
            [self.item.pk],
        )
        self.assertTrue(items_response.json()['required'])

        another_functionary = Usuario.objects.create_user(
            username='otro.funcionario.items',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=otro_cargo,
            delegacion=self.delegacion,
        )
        forbidden_response = self.client.get(
            '/actividad-diaria/items/',
            {'funcionario_id': another_functionary.pk},
        )
        self.assertEqual(forbidden_response.status_code, 403)

        self.client.force_login(self.supervisor)
        items_response = self.client.get(
            '/actividad-diaria/items/',
            {'funcionario_id': self.funcionario.pk},
        )
        self.assertEqual(items_response.status_code, 200)
        self.assertEqual(
            [item['id'] for item in items_response.json()['items']],
            [self.item.pk],
        )
        funcionario_sin_cargo = Usuario.objects.create_user(
            username='funcionario.sin.cargo',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        items_response = self.client.get(
            '/actividad-diaria/items/',
            {'funcionario_id': funcionario_sin_cargo.pk},
        )
        self.assertEqual(items_response.status_code, 200)
        self.assertEqual(items_response.json()['items'], [])
        self.assertFalse(items_response.json()['required'])
        self.client.force_login(self.funcionario)

        invalid_response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(item_medicion_id=str(inactivo.pk)),
        )
        self.assertIn('activity_error=item_invalido', invalid_response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_item_configuration_change_versions_its_measurement_period(self):
        admin = Usuario.objects.create_user(
            username='admin.item.version',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        response = self.client.post('/mantenedores/ponderaciones/', {
            'cargo': str(self.cargo.pk),
            'periodo': str(self.periodo.pk),
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': str(self.item.pk),
            'items-0-nombre_item': self.item.nombre_item,
            'items-0-descripcion': '',
            'items-0-tipo_unidad': 'UNIDAD',
            'items-0-meta_cuantitativa': '10',
            'items-0-ponderador': '60',
            'items-0-unidad_medida': 'Actividades',
            'items-0-activo': 'on',
            'items-1-nombre_item': 'Segundo ítem versionado',
            'items-1-descripcion': 'Ítem añadido al período',
            'items-1-tipo_unidad': 'UNIDAD',
            'items-1-meta_cuantitativa': '10',
            'items-1-ponderador': '40',
            'items-1-unidad_medida': 'Actividades',
            'items-1-activo': 'on',
        })

        self.assertRedirects(
            response,
            f'/mantenedores/ponderaciones/?cargo={self.cargo.pk}&periodo={self.periodo.pk}',
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.ponderador, 60)
        self.assertEqual(
            sum(ItemMedicion.objects.filter(
                cargo=self.cargo,
                periodo=self.periodo,
                activo=True,
            ).values_list('ponderador', flat=True)),
            100,
        )
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.version_de_parametro, '1.1')
        audit = RegistroAuditoria.objects.get(
            entidad='PeriodoMedicion',
            identificador_registro=str(self.periodo.pk),
            accion='VERSIONAR_PARAMETROS_PERIODO',
        )
        self.assertEqual(audit.valor_anterior['version_de_parametro'], '1.0')
        self.assertEqual(audit.valor_nuevo['version_de_parametro'], '1.1')
        version = VersionConfiguracionPeriodo.objects.get(
            periodo=self.periodo,
            version='1.1',
        )
        self.assertEqual(version.configuracion['periodo']['version_de_parametro'], '1.1')
        self.assertEqual(len(version.configuracion['items']), 2)
        self.assertEqual(
            version.configuracion['items'][0]['meta_cuantitativa'],
            10.0,
        )
        segundo_item = ItemMedicion.objects.get(
            periodo=self.periodo,
            nombre_item='Segundo ítem versionado',
        )
        response = self.client.post('/mantenedores/ponderaciones/', {
            'cargo': str(self.cargo.pk),
            'periodo': str(self.periodo.pk),
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '2',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': str(self.item.pk),
            'items-0-nombre_item': self.item.nombre_item,
            'items-0-descripcion': '',
            'items-0-tipo_unidad': 'UNIDAD',
            'items-0-meta_cuantitativa': '10',
            'items-0-ponderador': '100',
            'items-0-unidad_medida': 'Actividades',
            'items-0-activo': 'on',
            'items-1-id': str(segundo_item.pk),
            'items-1-nombre_item': segundo_item.nombre_item,
            'items-1-descripcion': segundo_item.descripcion,
            'items-1-tipo_unidad': segundo_item.tipo_unidad,
            'items-1-meta_cuantitativa': '10',
            'items-1-ponderador': '0',
            'items-1-unidad_medida': segundo_item.unidad_medida,
        })
        self.assertRedirects(
            response,
            f'/mantenedores/ponderaciones/?cargo={self.cargo.pk}&periodo={self.periodo.pk}',
        )
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.version_de_parametro, '1.2')
        version_reactivacion = VersionConfiguracionPeriodo.objects.get(
            periodo=self.periodo,
            version='1.2',
        )
        segundo_item.refresh_from_db()
        self.assertFalse(segundo_item.activo)
        self.assertFalse(
            version_reactivacion.configuracion['items'][1]['activo']
        )
        item_audit = RegistroAuditoria.objects.get(
            entidad='ItemMedicion',
            evento='CREACION',
        )
        self.assertEqual(item_audit.valor_nuevo['nombre_item'], 'Segundo ítem versionado')
        self.assertEqual(item_audit.valor_nuevo['meta_cuantitativa'], 10.0)

    def test_productivity_gap_is_calculated_per_cargo(self):
        resumenes = _agrupar_funcionarios_por_cargo([
            {
                'cargo_id': self.cargo.pk,
                'cargo': self.cargo.nombre,
                'total_ajustado_pct': 80,
                'meta_esperada_pct': 60,
            },
            {
                'cargo_id': self.cargo.pk,
                'cargo': self.cargo.nombre,
                'total_ajustado_pct': 50,
                'meta_esperada_pct': 40,
            },
        ])

        self.assertEqual(len(resumenes), 1)
        self.assertEqual(resumenes[0]['promedio_ponderado_pct'], 65)
        self.assertEqual(resumenes[0]['meta_esperada_pct'], 50)
        self.assertEqual(resumenes[0]['brecha_pct'], 15)

    def test_period_threshold_change_is_versioned_and_audited(self):
        admin = Usuario.objects.create_user(
            username='admin.periodo.version',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        response = self.client.post(
            f'/mantenedores/periodos/{self.periodo.pk}/modificar/',
            {
                'nombre': self.periodo.nombre,
                'anio': str(self.period_start.year),
                'trimestre': str((self.period_start.month - 1) // 3 + 1),
                'fecha_inicio': self.period_start.isoformat(),
                'fecha_termino': self.period_end.isoformat(),
                'dias_computables': str((self.period_end - self.period_start).days + 1),
                'umbral_semaforo_verde': '110',
                'umbral_semaforo_ambar': '60',
                'umbral_colectivo': '80',
                'tope_cumplimiento': '150',
                'estado': 'on',
            },
        )

        self.assertRedirects(response, '/mantenedores/periodos/')
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.umbral_semaforo_verde, 110)
        self.assertEqual(self.periodo.umbral_verde, 110)
        self.assertEqual(self.periodo.version_de_parametro, '1.1')
        audit = RegistroAuditoria.objects.get(
            entidad='PeriodoMedicion',
            identificador_registro=str(self.periodo.pk),
            evento='MODIFICACION',
        )
        self.assertEqual(audit.valor_anterior['umbral_semaforo_verde'], 100)
        self.assertEqual(audit.valor_nuevo['umbral_semaforo_verde'], 110)
        self.assertEqual(audit.valor_nuevo['version_de_parametro'], '1.1')
        version = VersionConfiguracionPeriodo.objects.get(
            periodo=self.periodo,
            version='1.1',
        )
        self.assertEqual(
            version.configuracion['periodo']['umbral_semaforo_verde'],
            110,
        )

    def test_closed_period_rejects_parameter_changes_but_can_be_reopened(self):
        self.periodo.cerrar_periodo()
        self.periodo.umbral_semaforo_verde = 110

        with self.assertRaises(ValidationError):
            self.periodo.save()

        self.periodo.refresh_from_db()
        self.assertTrue(self.periodo.cerrado)
        self.assertEqual(self.periodo.umbral_semaforo_verde, 100)

        self.periodo.reabrir_periodo()
        self.periodo.refresh_from_db()
        self.assertFalse(self.periodo.cerrado)

    def test_closed_period_rejects_item_meta_and_indicator_changes(self):
        meta = MetaModel.objects.create(
            nombre='Meta de prueba',
            valor_meta=10,
            ponderador=100,
            periodo=self.periodo,
            item=self.item,
            funcionario=self.funcionario,
            delegacion=self.delegacion,
        )
        indicador = Indicador.objects.create(
            avance=1,
            cumplimiento=10,
            ponderacion=100,
            meta=meta,
            periodo=self.periodo,
        )
        self.periodo.cerrar_periodo()

        self.item.meta_cuantitativa = 20
        with self.assertRaises(ValidationError):
            self.item.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.meta_cuantitativa, 10)

        meta.valor_meta = 20
        with self.assertRaises(ValidationError):
            meta.save()
        meta.refresh_from_db()
        self.assertEqual(meta.valor_meta, 10)

        indicador.avance = 2
        with self.assertRaises(ValidationError):
            indicador.save()
        indicador.refresh_from_db()
        self.assertEqual(indicador.avance, 1)

    def test_closed_period_parameter_form_rejects_configuration_changes(self):
        admin = Usuario.objects.create_user(
            username='admin.periodo.cerrado',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.periodo.cerrar_periodo()
        self.client.force_login(admin)

        response = self.client.post(
            f'/mantenedores/periodos/{self.periodo.pk}/modificar/',
            {
                'nombre': self.periodo.nombre,
                'anio': str(self.period_start.year),
                'trimestre': str((self.period_start.month - 1) // 3 + 1),
                'fecha_inicio': self.period_start.isoformat(),
                'fecha_termino': self.period_end.isoformat(),
                'dias_computables': str((self.period_end - self.period_start).days + 1),
                'umbral_semaforo_verde': '110',
                'umbral_semaforo_ambar': '60',
                'umbral_colectivo': '80',
                'tope_cumplimiento': '150',
                'cerrado': 'on',
                'estado': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('__all__', response.context['form'].errors)
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.umbral_semaforo_verde, 100)
        self.assertEqual(self.periodo.version_de_parametro, '1.0')

    def test_period_evaluator_uses_the_dashboard_thresholds(self):
        self.periodo.umbral_semaforo_verde = 110
        self.periodo.umbral_semaforo_ambar = 50
        self.periodo.umbral_verde = 80
        self.periodo.umbral_ambar = 40

        with patch.object(
            self.periodo,
            'calcular_meta_esperada_al_dia',
            return_value=50,
        ):
            self.assertEqual(self.periodo.evaluar_cumplimiento(50), 'AMBAR')
        with patch.object(
            self.periodo,
            'calcular_meta_esperada_al_dia',
            return_value=0,
        ):
            self.assertEqual(self.periodo.evaluar_cumplimiento(0), 'VERDE')
            self.assertEqual(self.periodo.evaluar_cumplimiento(-1), 'ROJO')

    def test_configuration_api_rejects_period_and_item_writes(self):
        admin = Usuario.objects.create_user(
            username='admin.config.api.readonly',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        api_client = APIClient()
        api_client.force_authenticate(user=admin)

        response = api_client.patch(
            f'/api/periodos/{self.periodo.pk}/',
            {'umbral_semaforo_verde': 120},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

        response = api_client.patch(
            f'/api/items-medicion/{self.item.pk}/',
            {'meta_cuantitativa': 20},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

        self.periodo.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.periodo.version_de_parametro, '1.0')
        self.assertEqual(self.item.meta_cuantitativa, 10)
        self.assertFalse(
            VersionConfiguracionPeriodo.objects.filter(periodo=self.periodo).exists()
        )

    def test_audit_records_cannot_be_updated_or_deleted(self):
        auditoria = RegistroAuditoria.objects.create(
            usuario=self.funcionario,
            funcionario=self.funcionario,
            evento='CREACION',
            accion='CREACION',
            entidad='Actividad',
            identificador_registro='1',
            valor_anterior=None,
            valor_nuevo={'solicitud': 'original'},
        )

        auditoria.valor_nuevo = {'solicitud': 'alterada'}
        with self.assertRaises(ValidationError):
            auditoria.save()
        with self.assertRaises(ValidationError):
            RegistroAuditoria.objects.filter(pk=auditoria.pk).update(
                valor_nuevo={'solicitud': 'alterada'},
            )
        with self.assertRaises(ValidationError):
            auditoria.delete()
        with self.assertRaises(ValidationError):
            RegistroAuditoria.objects.filter(pk=auditoria.pk).delete()

        auditoria.refresh_from_db()
        self.assertEqual(auditoria.valor_nuevo, {'solicitud': 'original'})
        with self.assertRaises(ProtectedError):
            self.funcionario.delete()
        self.assertTrue(RegistroAuditoria.objects.filter(pk=auditoria.pk).exists())
        admin = Usuario.objects.create_user(
            username='admin.audit.view',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)
        response = self.client.get('/auditoria/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CREACION')
        self.assertContains(response, 'Actividad')
        self.assertContains(response, 'solicitud')
        self.assertContains(
            response,
            timezone.localtime(auditoria.fecha).strftime('%d/%m/%Y %H:%M:%S'),
        )

    def test_administrator_can_create_a_delegado_with_required_role_and_scope(self):
        admin = Usuario.objects.create_user(
            username='admin.delegado.create',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Patricia',
            'apellidoPaterno': 'Gestora',
            'apellidoMaterno': 'Delegada',
            'correo': 'patricia.delegada@example.com',
            'rol': 'DELEGADO',
            'area_entidad': str(self.area.pk),
            'delegacion': str(self.delegacion.pk),
            'estado': 'on',
        })

        self.assertRedirects(response, '/mantenedores/funcionarios/')
        delegado = Usuario.objects.get(username='patricia.delegada@example.com')
        self.assertEqual(delegado.rol, Usuario.Rol.SUPERVISOR)
        self.assertTrue(delegado.es_delegado)
        self.assertEqual(delegado.delegacion_id, self.delegacion.pk)
        auditoria = RegistroAuditoria.objects.get(
            entidad='Usuario',
            identificador_registro=str(delegado.pk),
            evento='CREACION',
        )
        self.assertEqual(auditoria.valor_nuevo['rol'], Usuario.Rol.SUPERVISOR)
        self.assertTrue(auditoria.valor_nuevo['es_delegado'])
        self.assertEqual(auditoria.valor_nuevo['delegacion_id'], self.delegacion.pk)

    def test_delegado_form_rejects_missing_delegation_or_wrong_role(self):
        admin = Usuario.objects.create_user(
            username='admin.delegado.invalid',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Patricia',
            'apellidoPaterno': 'Gestora',
            'apellidoMaterno': 'Delegada',
            'correo': 'patricia.delegada@example.com',
            'rol': 'DELEGADO',
            'area_entidad': str(self.area.pk),
            'delegacion': '',
            'estado': 'on',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('delegacion', response.context['form'].errors)
        self.assertFalse(Usuario.objects.filter(email='patricia.delegada@example.com').exists())

        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Patricia',
            'apellidoPaterno': 'Gestora',
            'apellidoMaterno': 'Delegada',
            'correo': 'patricia.delegada@example.com',
            'rol': Usuario.Rol.ADMINISTRATIVO,
            'area_entidad': str(self.area.pk),
            'estado': 'on',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('rol', response.context['form'].errors)

    def test_period_form_rejects_end_date_before_start(self):
        admin = Usuario.objects.create_user(
            username='admin.periodo.fechas',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)
        fecha_termino = self.period_start - timedelta(days=1)

        response = self.client.post(
            f'/mantenedores/periodos/{self.periodo.pk}/modificar/',
            {
                'nombre': self.periodo.nombre,
                'anio': str(self.period_start.year),
                'trimestre': str((self.period_start.month - 1) // 3 + 1),
                'fecha_inicio': self.period_start.isoformat(),
                'fecha_termino': fecha_termino.isoformat(),
                'dias_computables': '1',
                'umbral_semaforo_verde': '100',
                'umbral_semaforo_ambar': '60',
                'umbral_colectivo': '80',
                'tope_cumplimiento': '150',
                'estado': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('fecha_termino', response.context['form'].errors)
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.fecha_termino, self.period_end)
        self.assertEqual(self.periodo.version_de_parametro, '1.0')

    def test_invalid_submission_preserves_form_values_after_redirect(self):
        values = self.activity_form(
            fecha_actividad=(self.today + timedelta(days=1)).isoformat(),
            solicitante_id='9999',
            direccion='Pasaje de prueba 456',
        )
        values['solicitante_id'] = str(self.solicitante.id)
        response = self.client.post('/actividad-diaria/registrar-actividad/', values)

        self.assertIn('activity_error=fecha_futura', response.url)
        page = self.client.get(response.url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'value="{values["fecha_actividad"]}"')
        self.assertContains(page, 'value="Pasaje de prueba 456"')
        self.assertContains(
            page,
            f'<option value="{self.solicitante.id}" data-contacto-telefono="{self.solicitante.telefono}" selected>',
        )
        self.assertContains(page, 'La fecha de la actividad no puede ser posterior a hoy.')
        self.assertNotIn('activity_form_data', self.client.session)

    def test_activity_page_selects_existing_requester(self):
        response = self.client.get(
            f'/actividad-diaria/registrar-actividad/?solicitante_id={self.solicitante.pk}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registrar actividad')
        self.assertContains(response, f'value="{self.solicitante.id}"')
        self.assertContains(
            response,
            f'name="contacto_telefono" type="tel" inputmode="tel"',
        )
        self.assertContains(response, f'value="{self.solicitante.telefono}"')
        self.assertContains(response, 'id="mapa-actividad"')
        self.assertContains(response, 'id="btn-ubicacion-actual"')
        self.assertContains(response, 'Cada actividad puede incluir un compromiso de seguimiento opcional')
        self.assertContains(response, 'name="compromiso_fecha"')
        self.assertNotContains(response, 'name="genera_compromiso"')
        self.assertNotContains(response, 'name="solicitante_run"')

    def test_non_functionary_can_open_activity_form_without_functionary_query(self):
        coordinator = Usuario.objects.create_user(
            username='coordinador.workflow',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(coordinator)

        response = self.client.get('/actividad-diaria/registrar-actividad/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="funcionario_id"')

    def test_requester_registration_creates_validated_requester(self):
        response = self.client.post('/actividad-diaria/registrar-solicitante/', {
            'run': '12.345.678-5',
            'nombre': 'Camila',
            'apellido_paterno': 'Rojas',
            'apellido_materno': 'Pérez',
            'tipo_solicitante': 'VECINO',
            'correo': 'camila@example.com',
            'telefono': '87654321',
        })

        self.assertEqual(response.status_code, 302)
        solicitante = Solicitante.objects.get(run='12.345.678-5')
        self.assertEqual(solicitante.telefono, '+56987654321')
        self.assertIn(f'solicitante_id={solicitante.id}', response.url)

    def test_requester_registration_shows_validation_errors_without_creating(self):
        response = self.client.post('/actividad-diaria/registrar-solicitante/', {
            'run': '12.345.678-0',
            'nombre': 'Camila',
            'apellido_paterno': 'Rojas',
            'apellido_materno': 'Pérez',
            'tipo_solicitante': 'VECINO',
            'correo': 'correo-invalido',
            'telefono': '99999999999999',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El dígito verificador del RUN no es válido.')
        self.assertFalse(Solicitante.objects.filter(nombre='Camila').exists())

    def test_requester_registration_rejects_blank_required_fields_and_invalid_choice(self):
        response = self.client.post('/actividad-diaria/registrar-solicitante/', {
            'run': '   ',
            'nombre': '   ',
            'apellido_paterno': '',
            'apellido_materno': '   ',
            'tipo_solicitante': 'NO_VALIDO',
            'correo': '   ',
            'telefono': '',
        })

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        for field in (
            'run',
            'nombre',
            'apellido_paterno',
            'apellido_materno',
            'tipo_solicitante',
            'correo',
        ):
            with self.subTest(field=field):
                self.assertIn(field, form.errors)
        self.assertFalse(Solicitante.objects.filter(nombre='').exists())

    def test_requester_registration_rejects_unformatted_run_bad_email_phone_and_duplicate_run(self):
        cases = (
            ('run', '12345678-5'),
            ('correo', 'correo-invalido'),
            ('telefono', '999999999'),
            ('run', self.solicitante.run),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = {
                    'run': '12.345.678-5',
                    'nombre': 'Camila',
                    'apellido_paterno': 'Rojas',
                    'apellido_materno': 'Pérez',
                    'tipo_solicitante': 'VECINO',
                    'correo': 'camila@example.com',
                    'telefono': '9 8765 4321',
                }
                data[field] = value
                response = self.client.post('/actividad-diaria/registrar-solicitante/', data)

                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
                self.assertFalse(Solicitante.objects.filter(nombre='Camila').exists())

    def test_commitment_date_outside_period_is_rejected(self):
        invalid_date = self.period_end + timedelta(days=1)
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(
                compromiso_fecha=invalid_date.isoformat(),
                compromiso_territorio='Sector Centro',
                compromiso_area_apoyo='Operaciones y Territorio',
                compromiso_descripcion='Seguimiento de la solicitud',
            ),
        )

        self.assertIn('activity_error=fecha_compromiso', response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_commitment_rejects_invalid_selector_values_and_blank_description(self):
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(
                compromiso_fecha=self.today.isoformat(),
                compromiso_territorio='Territorio inventado',
                compromiso_area_apoyo='Área inventada',
                compromiso_descripcion='   ',
            ),
        )

        self.assertIn('activity_error=compromiso_datos', response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_evidence_only_route_does_not_modify_activity_fields(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Dato original',
            direccion='Dirección original',
        )
        data = {'fotos': [self.upload('evidencia.jpg')]}
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post(
                f'/personal/actividades/{actividad.id}/evidencias/',
                data,
            )

        self.assertEqual(response.status_code, 302)
        actividad.refresh_from_db()
        self.assertEqual(actividad.actividad_solicitud, 'Dato original')
        self.assertEqual(actividad.direccion, 'Dirección original')
        self.assertEqual(actividad.evidencias.count(), 1)

    def test_evidence_status_and_compromise_date_stay_synchronized(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Solicitante de prueba',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            actividad=actividad,
        )
        actividad.compromiso_agenda = compromiso
        actividad.save(update_fields=['compromiso_agenda'])
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        compromiso.sincronizar_estado_evidencias()
        self.assertEqual(compromiso.estado_tablero, CompromisoAgenda.Estado.EN_PROCESO)

        evidencia.estado = Evidencia.Estado.APROBADA
        evidencia.save(update_fields=['estado'])
        compromiso.sincronizar_estado_evidencias()
        self.assertEqual(compromiso.estado_tablero, CompromisoAgenda.Estado.COMPLETADO)

        compromiso.fecha_compromiso = self.today - timedelta(days=1)
        compromiso.fecha_comprometida = compromiso.fecha_compromiso
        compromiso.estado = CompromisoAgenda.Estado.EN_PROCESO
        compromiso.save()
        evidencia.estado = Evidencia.Estado.PENDIENTE
        evidencia.save(update_fields=['estado'])
        self.assertEqual(compromiso.estado_tablero, CompromisoAgenda.Estado.VENCIDO)

    def test_editing_commitment_updates_both_date_fields(self):
        coordinator = Usuario.objects.create_user(
            username='coordinador.compromiso.editar',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(coordinator)
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            fecha_actividad=self.today,
        )
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Solicitante de prueba',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            actividad=actividad,
        )
        nueva_fecha = self.period_end
        response = self.client.post(
            f'/tubo/{compromiso.id}/editar/',
            {
                'solicitante': 'Solicitante actualizado',
                'territorio': 'Sector Centro',
                'fecha_comprometida': nueva_fecha.isoformat(),
                'actividad_id': str(actividad.id),
                'area_apoyo': 'Operaciones y Territorio',
                'descripcion': 'Seguimiento actualizado',
                'observaciones': 'Fecha corregida',
            },
        )

        self.assertEqual(response.status_code, 302)
        compromiso.refresh_from_db()
        self.assertEqual(compromiso.fecha_compromiso, nueva_fecha)
        self.assertEqual(compromiso.fecha_comprometida, nueva_fecha)
        actividad.refresh_from_db()
        self.assertEqual(actividad.compromiso_agenda_id, compromiso.pk)

    def test_tube_commitment_form_can_link_an_existing_activity(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Atención pendiente de seguimiento',
        )
        response = self.client.post('/tubo/crear/', {
            'solicitante': 'Solicitante de prueba',
            'origen': 'EXT',
            'territorio': 'Sector Centro',
            'delegacion': str(self.delegacion.pk),
            'responsable': str(self.funcionario.pk),
            'fecha_comprometida': self.today.isoformat(),
            'actividad': str(actividad.pk),
            'area_apoyo': 'Operaciones y Territorio',
            'descripcion': 'Dar seguimiento a la solicitud',
            'estado': CompromisoAgenda.Estado.PENDIENTE,
            'observacion': '',
        }, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/tubo/')
        compromiso = CompromisoAgenda.objects.get(actividad=actividad)
        actividad.refresh_from_db()
        self.assertEqual(actividad.compromiso_agenda_id, compromiso.pk)

    def test_commitment_form_preselects_activity_from_personal_activity_link(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Actividad para asignar',
        )

        response = self.client.get(
            f'/tubo/crear/?actividad_id={actividad.pk}',
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<option value="{actividad.pk}" selected>')

    def test_tube_new_commitment_modal_lists_available_activities(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Actividad disponible',
        )

        response = self.client.get('/tubo/', secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="actividad"')
        self.assertContains(
            response,
            f'<option value="{actividad.pk}" data-delegacion="{self.delegacion.pk}">',
        )

    def test_tube_commitment_form_rejects_activity_from_another_functionary(self):
        another_functionary = Usuario.objects.create_user(
            username='otro.funcionario.compromiso',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            cargo=self.cargo,
            delegacion=self.delegacion,
        )
        actividad = Actividad.objects.create(
            funcionario=another_functionary,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )

        response = self.client.post('/tubo/crear/', {
            'solicitante': 'Solicitante de prueba',
            'origen': 'EXT',
            'territorio': 'Sector Centro',
            'delegacion': str(self.delegacion.pk),
            'responsable': str(self.funcionario.pk),
            'fecha_comprometida': self.today.isoformat(),
            'actividad': str(actividad.pk),
            'area_apoyo': 'Operaciones y Territorio',
            'descripcion': 'Dar seguimiento a la solicitud',
            'estado': CompromisoAgenda.Estado.PENDIENTE,
            'observacion': '',
        }, secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CompromisoAgenda.objects.filter(actividad=actividad).exists())

    def test_workflow_pages_render(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            direccion='Dirección de prueba',
            latitud=-29.9,
            longitud=-71.2,
        )
        CompromisoAgenda.objects.create(
            solicitante='Solicitante de prueba',
            territorio='Territorio histórico',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            actividad=actividad,
        )

        personal_page = self.client.get('/personal/')
        self.assertContains(personal_page, 'Dirección de prueba')
        self.assertNotContains(personal_page, 'modalRegistroActividad')
        self.assertContains(personal_page, 'Actividad Diaria')
        self.assertContains(self.client.get('/tubo/'), 'Territorio histórico')
        self.assertContains(self.client.get('/terreno/'), 'Dirección de prueba')

    def test_terrain_existing_activity_option_includes_fields_for_prefill(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            direccion='Avenida del Mar 123',
            latitud=-29.9,
            longitud=-71.2,
            accion_ejecutada='Se reparó una luminaria',
        )

        response = self.client.get('/terreno/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-direccion="Avenida del Mar 123"')
        self.assertContains(response, 'data-accion="Se reparó una luminaria"')
        self.assertContains(response, f'data-item-id="{self.item.id}"')
        self.assertContains(response, f'data-item-name="{self.item.nombre_item}"')
        self.assertContains(response, 'id="activity-task-section"')
        self.assertContains(response, 'id="activity-location-section"')
        self.assertContains(response, 'programarBusquedaDireccionCercana')
        self.assertContains(response, 'Buscando la dirección más cercana')

    def test_terrain_evidence_upload_keeps_existing_activity_location(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            direccion='Avenida del Mar 123',
            latitud=-29.9,
            longitud=-71.2,
        )

        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post('/terreno/', {
                'actividad_id': str(actividad.id),
                'fotos': [self.upload('evidencia-terreno.jpg')],
            })

        self.assertEqual(response.status_code, 302)
        actividad.refresh_from_db()
        self.assertEqual(actividad.direccion, 'Avenida del Mar 123')
        self.assertEqual(actividad.latitud, -29.9)
        self.assertEqual(actividad.longitud, -71.2)
        self.assertEqual(actividad.evidencias.count(), 1)

    def test_terrain_activity_and_evidence_roll_back_if_audit_fails(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            with patch(
                'sgr_app.web_views.RegistroAuditoria.objects.create',
                side_effect=IntegrityError('audit insert failed'),
            ):
                with self.assertRaises(IntegrityError):
                    self.client.post('/terreno/', {
                        'direccion': 'Pasaje de prueba 456',
                        'accion_ejecutada': 'Inspección de luminaria',
                        'contacto_telefono': '12345678',
                        'item_medicion': str(self.item.pk),
                        'fotos': [self.upload('foto-terreno-rollback.jpg')],
                    })

        self.assertFalse(Actividad.objects.exists())
        self.assertFalse(Evidencia.objects.exists())

    def test_terrain_activity_requires_contact_phone(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post('/terreno/', {
                'direccion': 'Pasaje de prueba 456',
                'accion_ejecutada': 'Inspección de luminaria',
                'fotos': [self.upload('foto-terreno-sin-telefono.jpg')],
            })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Actividad.objects.exists())
        error_page = self.client.get(response.url)
        self.assertContains(error_page, 'Teléfono de contacto: Este campo es obligatorio.')

    def test_terrain_activity_rejects_a_malformed_measurement_item_id(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post('/terreno/', {
                'direccion': 'Pasaje de prueba 456',
                'accion_ejecutada': 'Inspección de luminaria',
                'contacto_telefono': '12345678',
                'item_medicion': 'no-es-un-id',
                'fotos': [self.upload('foto-terreno-item-invalido.jpg')],
            })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Actividad.objects.exists())
        error_page = self.client.get(response.url)
        self.assertContains(
            error_page,
            'Ítem de medición: No corresponde al período o cargo actual.',
        )

    def test_terrain_activity_persists_activity_evidence_and_audit(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            response = self.client.post('/terreno/', {
                'direccion': 'Pasaje de prueba 456',
                'accion_ejecutada': 'Inspección de luminaria',
                'contacto_telefono': '12345678',
                'item_medicion': str(self.item.pk),
                'fotos': [self.upload('foto-terreno-ok.jpg')],
            })

        self.assertEqual(response.status_code, 302)
        actividad = Actividad.objects.get(funcionario=self.funcionario)
        self.assertEqual(actividad.contacto_telefono, '+56912345678')
        self.assertEqual(actividad.evidencias.count(), 1)
        audit = RegistroAuditoria.objects.get(
            entidad='Actividad',
            identificador_registro=str(actividad.pk),
            accion='REGISTRO_ACTIVIDAD_TERRENO',
        )
        self.assertEqual(audit.valor_nuevo['delegacion_id'], self.delegacion.pk)
        self.assertEqual(len(audit.valor_nuevo['evidencias']), 1)

    def test_web_evidence_validation_updates_commitment_and_audit(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Solicitante de validación',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            actividad=actividad,
            estado=CompromisoAgenda.Estado.PENDIENTE,
        )
        actividad.compromiso_agenda = compromiso
        actividad.save(update_fields=['compromiso_agenda'])
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        compromiso.estado = CompromisoAgenda.Estado.PENDIENTE
        compromiso.save(update_fields=['estado'])
        supervisor = Usuario.objects.create_user(
            username='supervisor.validacion.success',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(supervisor)

        response = self.client.post(
            f'/verificacion/{evidencia.pk}/validar/',
            {
                'decision': Evidencia.Estado.APROBADA,
                'observacion': 'Evidencia revisada',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/verificacion/')
        evidencia.refresh_from_db()
        compromiso.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.APROBADA)
        self.assertEqual(evidencia.verificador_id, supervisor.pk)
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.COMPLETADO)
        audit = RegistroAuditoria.objects.get(
            entidad='Evidencia',
            identificador_registro=str(evidencia.pk),
            accion='VALIDAR_EVIDENCIA',
        )
        self.assertEqual(audit.valor_anterior['estado'], Evidencia.Estado.PENDIENTE)
        self.assertEqual(audit.valor_nuevo['estado'], Evidencia.Estado.APROBADA)

    def test_evidence_validation_recalculates_related_meta_indicator(self):
        meta = MetaModel.objects.create(
            nombre='Meta de evidencia aprobada',
            valor_meta=10,
            periodo=self.periodo,
            item=self.item,
            funcionario=self.funcionario,
            delegacion=self.delegacion,
        )
        indicador = Indicador.objects.create(
            meta=meta,
            periodo=self.periodo,
            avance=0,
            cumplimiento=0,
        )
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        supervisor = Usuario.objects.create_user(
            username='supervisor.validacion.meta',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(supervisor)

        response = self.client.post(
            f'/verificacion/{evidencia.pk}/validar/',
            {'decision': Evidencia.Estado.APROBADA},
        )

        self.assertEqual(response.status_code, 302)
        indicador.refresh_from_db()
        self.assertEqual(indicador.avance, 1)
        self.assertEqual(indicador.cumplimiento, 10)
        self.assertEqual(meta.calcular_meta(), 10)
        audit = RegistroAuditoria.objects.get(
            entidad='Evidencia',
            identificador_registro=str(evidencia.pk),
            accion='VALIDAR_EVIDENCIA',
        )
        self.assertEqual(audit.valor_nuevo['metas'][0]['avance_aprobado'], 1)

    def test_administrative_user_cannot_validate_evidence(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )

        response = self.client.post(
            f'/verificacion/{evidencia.pk}/validar/',
            {'decision': Evidencia.Estado.APROBADA},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/verificacion/')
        evidencia.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.PENDIENTE)
        self.assertFalse(RegistroAuditoria.objects.filter(
            entidad='Evidencia',
            identificador_registro=str(evidencia.pk),
            accion='VALIDAR_EVIDENCIA',
        ).exists())

    def test_evidence_rejection_requires_a_reason(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        supervisor = Usuario.objects.create_user(
            username='supervisor.validacion.motivo',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(supervisor)

        response = self.client.post(
            f'/verificacion/{evidencia.pk}/validar/',
            {
                'decision': Evidencia.Estado.RECHAZADA,
                'observacion': '  ',
            },
        )

        self.assertEqual(response.status_code, 302)
        evidencia.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.PENDIENTE)
        self.assertFalse(RegistroAuditoria.objects.filter(
            entidad='Evidencia',
            identificador_registro=str(evidencia.pk),
            accion='VALIDAR_EVIDENCIA',
        ).exists())

    def test_web_evidence_validation_can_request_correction_with_reason(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        delegado = Usuario.objects.create_user(
            username='delegado.validacion.correccion',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            es_delegado=True,
            delegacion=self.delegacion,
        )
        self.client.force_login(delegado)

        response = self.client.post(
            f'/verificacion/{evidencia.pk}/validar/',
            {
                'decision': Evidencia.Estado.CORRECCION,
                'observacion': 'Adjuntar una foto legible.',
            },
        )

        self.assertRedirects(response, '/verificacion/')
        evidencia.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.CORRECCION)
        self.assertEqual(evidencia.verificador, delegado)
        self.assertEqual(
            evidencia.observacion_rechazo,
            'Adjuntar una foto legible.',
        )
        self.assertIsNotNone(evidencia.fecha_validacion)
        self.assertTrue(RegistroAuditoria.objects.filter(
            entidad='Evidencia',
            identificador_registro=str(evidencia.pk),
            usuario=delegado,
            valor_nuevo__estado=Evidencia.Estado.CORRECCION,
        ).exists())

    def test_evidence_validation_rolls_back_evidence_and_commitment_if_audit_fails(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
        )
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Solicitante de prueba',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            actividad=actividad,
            estado=CompromisoAgenda.Estado.PENDIENTE,
        )
        actividad.compromiso_agenda = compromiso
        actividad.save(update_fields=['compromiso_agenda'])
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.PENDIENTE,
        )
        compromiso.estado = CompromisoAgenda.Estado.PENDIENTE
        compromiso.save(update_fields=['estado'])
        supervisor = Usuario.objects.create_user(
            username='supervisor.validacion.atomic',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(supervisor)

        with patch(
            'sgr_app.web_views.RegistroAuditoria.objects.create',
            side_effect=IntegrityError('audit insert failed'),
        ):
            with self.assertRaises(IntegrityError):
                self.client.post(
                    f'/verificacion/{evidencia.pk}/validar/',
                    {'decision': Evidencia.Estado.APROBADA},
                )

        evidencia.refresh_from_db()
        compromiso.refresh_from_db()
        self.assertEqual(evidencia.estado, Evidencia.Estado.PENDIENTE)
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.PENDIENTE)

    def test_commitment_creation_rolls_back_if_audit_fails(self):
        with patch(
            'sgr_app.web_views.RegistroAuditoria.objects.create',
            side_effect=IntegrityError('audit insert failed'),
        ):
            with self.assertRaises(IntegrityError):
                self.client.post('/tubo/crear/', {
                    'solicitante': 'Solicitante nuevo',
                    'origen': 'EXT',
                    'territorio': 'Sector Centro',
                    'delegacion': str(self.delegacion.pk),
                    'responsable': str(self.funcionario.pk),
                    'fecha_comprometida': self.today.isoformat(),
                    'area_apoyo': 'Operaciones y Territorio',
                    'descripcion': 'Compromiso de prueba',
                    'estado': CompromisoAgenda.Estado.PENDIENTE,
                })

        self.assertFalse(CompromisoAgenda.objects.exists())

    def test_commitment_creation_saves_audit_in_the_same_transaction(self):
        response = self.client.post('/tubo/crear/', {
            'solicitante': 'Vecindario Centro',
            'origen': 'EXT',
            'territorio': 'Sector Centro',
            'delegacion': str(self.delegacion.pk),
            'responsable': str(self.funcionario.pk),
            'fecha_comprometida': self.today.isoformat(),
            'area_apoyo': 'Operaciones y Territorio',
            'descripcion': 'Revisar luminarias de la plaza',
            'estado': CompromisoAgenda.Estado.PENDIENTE,
            'observacion': 'Creado en prueba',
        })

        self.assertRedirects(response, '/tubo/')
        compromiso = CompromisoAgenda.objects.get(solicitante='Vecindario Centro')
        auditoria = RegistroAuditoria.objects.get(
            entidad='CompromisoAgenda',
            identificador_registro=str(compromiso.pk),
            accion='CREAR_COMPROMISO',
        )
        self.assertIsNone(auditoria.valor_anterior)
        self.assertEqual(auditoria.valor_nuevo['responsable_id'], self.funcionario.pk)
        self.assertEqual(compromiso.fecha_compromiso, self.today)
        observacion = ObservacionCompromiso.objects.get(compromiso=compromiso)
        self.assertEqual(observacion.autor_id, self.funcionario.pk)
        self.assertEqual(observacion.texto, 'Creado en prueba')

    def test_commitment_creation_requires_responsible_and_date(self):
        response = self.client.post('/tubo/crear/', {
            'solicitante': 'Vecindario sin asignar',
            'origen': 'EXT',
            'territorio': 'Sector Centro',
            'delegacion': str(self.delegacion.pk),
            'responsable': '',
            'fecha_comprometida': '',
            'area_apoyo': 'Operaciones y Territorio',
            'descripcion': 'Compromiso sin responsable ni fecha',
            'estado': CompromisoAgenda.Estado.PENDIENTE,
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('responsable', response.context['form'].errors)
        self.assertIn('fecha_comprometida', response.context['form'].errors)
        self.assertFalse(CompromisoAgenda.objects.exists())

    def test_tubo_period_filter_keeps_overdue_detail_and_summary_in_sync(self):
        anterior_fin = self.period_start - timedelta(days=1)
        anterior_inicio = date(
            anterior_fin.year,
            ((anterior_fin.month - 1) // 3) * 3 + 1,
            1,
        )
        periodo_anterior = PeriodoMedicion.objects.create(
            nombre='Período anterior para agenda',
            fecha_inicio=anterior_inicio,
            fecha_termino=anterior_fin,
        )
        CompromisoAgenda.objects.create(
            solicitante='Vecindario del período activo',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            estado=CompromisoAgenda.Estado.PENDIENTE,
        )
        CompromisoAgenda.objects.create(
            solicitante='Vecindario del período anterior',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            fecha_compromiso=anterior_fin,
            fecha_comprometida=anterior_fin,
            estado=CompromisoAgenda.Estado.EN_PROCESO,
        )

        periodo_actual_response = self.client.get('/tubo/')
        self.assertEqual(periodo_actual_response.status_code, 200)
        self.assertEqual(len(periodo_actual_response.context['todos_los_compromisos']), 1)
        self.assertEqual(periodo_actual_response.context['totales_colectivos']['total'], 1)

        periodo_anterior_response = self.client.get(
            f'/tubo/?periodo={periodo_anterior.pk}'
        )
        self.assertEqual(periodo_anterior_response.status_code, 200)
        self.assertEqual(len(periodo_anterior_response.context['todos_los_compromisos']), 1)
        self.assertEqual(periodo_anterior_response.context['totales_colectivos']['total'], 1)
        self.assertEqual(periodo_anterior_response.context['totales_colectivos']['vencido'], 1)
        self.assertEqual(len(periodo_anterior_response.context['vencidos']), 1)
        self.assertContains(periodo_anterior_response, 'Vencido')

        todos_response = self.client.get('/tubo/?periodo=todos')
        self.assertEqual(todos_response.status_code, 200)
        self.assertEqual(len(todos_response.context['todos_los_compromisos']), 2)
        self.assertEqual(todos_response.context['totales_colectivos']['total'], 2)
        self.assertEqual(todos_response.context['totales_colectivos']['vencido'], 1)

    def test_shared_agenda_exposes_same_delegation_commitments_but_not_other_delegations(self):
        colega = Usuario.objects.create_user(
            username='funcionario.agenda.colega',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        delegacion_ajena = Delegacion.objects.create(
            nombre='Delegación ajena',
            codigo='AJN',
        )
        CompromisoAgenda.objects.create(
            solicitante='Vecino visible para delegación',
            delegacion=self.delegacion,
            responsable=colega,
            creado_por=colega,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            descripcion='Compromiso compartido',
        )
        CompromisoAgenda.objects.create(
            solicitante='Vecino de otra delegación',
            delegacion=delegacion_ajena,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            descripcion='Compromiso de otro territorio',
        )
        delegado = Usuario.objects.create_user(
            username='delegado.agenda.compartida',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            es_delegado=True,
            delegacion=self.delegacion,
        )
        self.client.force_login(delegado)

        response = self.client.get('/tubo/?periodo=todos')

        self.assertEqual(response.status_code, 200)
        compromiso_ids = {
            compromiso.pk
            for compromiso in response.context['todos_los_compromisos']
        }
        visible = CompromisoAgenda.objects.get(
            solicitante='Vecino visible para delegación',
        )
        self.assertIn(visible.pk, compromiso_ids)
        self.assertEqual(len(compromiso_ids), 1)

    def test_operational_alerts_identify_overdue_low_performance_and_inactivity(self):
        vencido = CompromisoAgenda.objects.create(
            solicitante='Vecino con compromiso vencido',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.supervisor,
            fecha_compromiso=self.today - timedelta(days=1),
            fecha_comprometida=self.today - timedelta(days=1),
            descripcion='Revisar luminaria pendiente',
            estado=CompromisoAgenda.Estado.EN_PROCESO,
        )
        delegado = Usuario.objects.create_user(
            username='delegado.alertas.operativas',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            es_delegado=True,
            delegacion=self.delegacion,
        )
        self.client.force_login(delegado)

        response = self.client.get(
            f'/alertas/?periodo={self.periodo.pk}',
        )

        self.assertEqual(response.status_code, 200)
        alertas_por_tipo = {
            alerta['tipo']: alerta
            for alerta in response.context['alertas']
        }
        self.assertIn('VENCIMIENTO', alertas_por_tipo)
        self.assertIn('RENDIMIENTO_BAJO', alertas_por_tipo)
        self.assertIn('INACTIVIDAD', alertas_por_tipo)
        alerta_vencimiento = alertas_por_tipo['VENCIMIENTO']
        self.assertEqual(alerta_vencimiento['responsable'], self.funcionario.username)
        self.assertEqual(alerta_vencimiento['fecha'], vencido.fecha_compromiso)
        self.assertIn(f'compromiso-{vencido.pk}', alerta_vencimiento['url'])
        self.assertEqual(alertas_por_tipo['INACTIVIDAD']['fecha'], self.periodo.fecha_inicio)

    def test_pending_evidence_alert_shows_responsible_date_and_direct_link(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Revisar luminaria reportada',
        )
        evidencia = Evidencia.objects.create(
            actividad=actividad,
            subido_por=self.funcionario,
            estado=Evidencia.Estado.PENDIENTE,
        )

        response = self.client.get(f'/alertas/?periodo={self.periodo.pk}')

        self.assertEqual(response.status_code, 200)
        alerta = next(
            alerta for alerta in response.context['alertas']
            if alerta['tipo'] == 'EVIDENCIA_PENDIENTE'
        )
        responsable = self.funcionario.get_full_name() or self.funcionario.username
        self.assertEqual(alerta['responsable'], responsable)
        self.assertEqual(alerta['fecha'], evidencia.fecha_subida)
        self.assertIn(f'evidencia-{evidencia.pk}', alerta['url'])

    def test_activity_metrics_recalculate_after_a_validated_activity_is_added(self):
        anterior = calcular_metricas_funcionario(
            self.funcionario,
            self.periodo,
            self.today,
        )
        self.assertEqual(anterior['total_actividades'], 0)
        self.assertIsNone(anterior['ultima_actividad'])

        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Actividad validada de prueba',
        )
        Evidencia.objects.create(
            actividad=actividad,
            estado=Evidencia.Estado.APROBADA,
        )

        actual = calcular_metricas_funcionario(
            self.funcionario,
            self.periodo,
            self.today,
        )
        self.assertEqual(actual['total_actividades'], 1)
        self.assertEqual(actual['items'][0]['avance_aprobado'], 1)
        self.assertIsNotNone(actual['ultima_actividad'])
        self.assertEqual(actual['dias_sin_ingreso'], 0)

    def test_operational_report_filters_and_csv_keep_selected_criteria(self):
        activity_match = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Reparar luminaria sector norte',
            accion_ejecutada='Luminaria reparada',
        )
        otra_delegacion = Delegacion.objects.create(
            nombre='Delegación para informe',
            codigo='INF',
        )
        funcionario_ajeno = Usuario.objects.create_user(
            username='funcionario.informe.ajeno',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=otra_delegacion,
        )
        Actividad.objects.create(
            funcionario=funcionario_ajeno,
            delegacion=otra_delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Otra solicitud',
        )
        self.client.force_login(self.supervisor)
        filtros = (
            f'periodo={self.periodo.pk}&delegacion={self.delegacion.pk}'
            f'&funcionario={self.funcionario.pk}&item={self.item.pk}&q=luminaria'
        )

        html_response = self.client.get(f'/informes/?{filtros}')
        self.assertEqual(html_response.status_code, 200)
        self.assertEqual(html_response.context['total_resultados'], 1)
        self.assertEqual(
            html_response.context['pagina'][0].pk,
            activity_match.pk,
        )

        csv_response = self.client.get(f'/informes/?{filtros}&formato=csv')
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('text/csv', csv_response['Content-Type'])
        self.assertIn('attachment;', csv_response['Content-Disposition'])
        contenido = csv_response.content.decode('utf-8-sig')
        self.assertIn('Generado', contenido)
        self.assertIn('luminaria', contenido)
        self.assertIn('Reparar luminaria sector norte', contenido)
        self.assertNotIn('Otra solicitud', contenido)

    def test_operational_report_handles_unconfigured_current_period(self):
        admin = Usuario.objects.create_user(
            username='admin.informe.sin.periodo',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        periodo_no_guardado = PeriodoMedicion(
            nombre='Período actual sin configurar',
            fecha_inicio=self.period_start,
            fecha_termino=self.period_end,
        )
        self.client.force_login(admin)

        with patch(
            'sgr_app.web_views.get_periodo_activo',
            return_value=periodo_no_guardado,
        ):
            response = self.client.get('/informes/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_resultados'], 0)
        self.assertFalse(response.context['items'].exists())

    def test_operational_report_applies_each_filter_independently(self):
        otro_item = ItemMedicion.objects.create(
            cargo=self.cargo,
            periodo=self.periodo,
            nombre_item='Ítem alternativo para informe',
            meta_cuantitativa=10,
            ponderador=0,
        )
        otro_funcionario = Usuario.objects.create_user(
            username='funcionario.filtro.informe',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        otra_delegacion = Delegacion.objects.create(
            nombre='Delegación filtro informe',
            codigo='FIR',
        )
        funcionario_otra_delegacion = Usuario.objects.create_user(
            username='funcionario.otra.delegacion.informe',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=otra_delegacion,
        )
        actividad_objetivo = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.today,
            actividad_solicitud='Reparación de luminaria',
        )
        actividad_otro_funcionario = Actividad.objects.create(
            funcionario=otro_funcionario,
            delegacion=self.delegacion,
            item_medicion=otro_item,
            fecha_actividad=self.today,
            actividad_solicitud='Poda de árbol',
        )
        actividad_otra_delegacion = Actividad.objects.create(
            funcionario=funcionario_otra_delegacion,
            delegacion=otra_delegacion,
            item_medicion=otro_item,
            fecha_actividad=self.today,
            actividad_solicitud='Mantención de plaza',
        )
        actividad_fuera_periodo = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            fecha_actividad=self.period_start - timedelta(days=1),
            actividad_solicitud='Luminaria en período anterior',
        )
        self.client.force_login(self.supervisor)

        filtros_esperados = (
            (
                {'periodo': self.periodo.pk},
                {actividad_objetivo.pk, actividad_otro_funcionario.pk, actividad_otra_delegacion.pk},
            ),
            (
                {'periodo': self.periodo.pk, 'delegacion': self.delegacion.pk},
                {actividad_objetivo.pk, actividad_otro_funcionario.pk},
            ),
            (
                {'periodo': self.periodo.pk, 'funcionario': self.funcionario.pk},
                {actividad_objetivo.pk},
            ),
            (
                {'periodo': self.periodo.pk, 'item': self.item.pk},
                {actividad_objetivo.pk},
            ),
            (
                {'periodo': self.periodo.pk, 'q': 'luminaria'},
                {actividad_objetivo.pk},
            ),
        )
        for filtros, ids_esperados in filtros_esperados:
            with self.subTest(filtros=filtros):
                response = self.client.get('/informes/', filtros)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {actividad.pk for actividad in response.context['pagina']},
                    ids_esperados,
                )

    def test_social_case_web_flow_records_three_ordered_attentions_on_one_case(self):
        self.client.force_login(self.supervisor)
        response = self.client.post('/casos-sociales/', {
            'action': 'crear_caso',
            'codigo_caso': 'CASO-WEB-001',
            'persona_anonimizada': 'PERSONA-ANON-001',
            'delegacion': str(self.delegacion.pk),
            'fecha_apertura': self.today.isoformat(),
        })

        self.assertRedirects(response, '/casos-sociales/')
        caso = CasoSocial.objects.get(codigo_caso='CASO-WEB-001')
        for etapa in range(1, 4):
            response = self.client.post('/casos-sociales/', {
                'action': 'registrar_atencion',
                'caso_id': str(caso.pk),
                'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
                'detalle': f'Detalle de gestión {etapa}',
                'resultado': f'Resultado {etapa}',
                'fecha_gestion': self.today.isoformat(),
            })
            self.assertRedirects(response, '/casos-sociales/')

        caso.refresh_from_db()
        atenciones = list(caso.atenciones.all())
        self.assertEqual([atencion.etapa for atencion in atenciones], [1, 2, 3])
        self.assertEqual({atencion.funcionario_id for atencion in atenciones}, {self.supervisor.pk})
        self.assertEqual(caso.persona_anonimizada, 'PERSONA-ANON-001')
        self.assertTrue(all(atencion.caso_id == caso.pk for atencion in atenciones))
        self.assertEqual(atenciones[0].fecha_visita_programada, self.today)
        self.assertEqual(atenciones[1].fecha_visita, self.today)
        self.assertEqual(atenciones[2].fecha_entrega_beneficio, self.today)

        response = self.client.post('/casos-sociales/', {
            'action': 'registrar_atencion',
            'caso_id': str(caso.pk),
            'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
            'detalle': 'No debe registrarse',
            'resultado': 'No corresponde',
            'fecha_gestion': self.today.isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no puede registrar más de 3 gestiones')
        self.assertEqual(AtencionSocial.objects.filter(caso=caso).count(), 3)
        self.assertContains(response, 'PERSONA-ANON-001')
        self.assertContains(response, 'Gestiones registradas: 3 de 3')

    def test_social_case_api_records_sequential_attentions_and_caps_at_three(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.supervisor)
        response = api_client.post('/api/casos-sociales/', {
            'codigo_caso': 'CASO-API-001',
            'persona_anonimizada': 'PERSONA-API-001',
            'delegacion': self.delegacion.pk,
            'fecha_apertura': self.today.isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        caso = CasoSocial.objects.get(pk=response.data['id'])

        fecha_por_etapa = {
            1: 'fecha_visita_programada',
            2: 'fecha_visita',
            3: 'fecha_entrega_beneficio',
        }
        for etapa in range(1, 4):
            response = api_client.post('/api/atenciones-sociales/', {
                'caso': caso.pk,
                'etapa': etapa,
                'tipo_atencion_catalogo': self.tipo_atencion_catalogo.pk,
                'detalle': f'Detalle API {etapa}',
                'resultado': f'Resultado API {etapa}',
                fecha_por_etapa[etapa]: self.today.isoformat(),
            }, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['funcionario'], self.supervisor.pk)

        response = api_client.post('/api/atenciones-sociales/', {
            'caso': caso.pk,
            'etapa': 3,
            'tipo_atencion_catalogo': self.tipo_atencion_catalogo.pk,
            'detalle': 'Gestión adicional',
            'resultado': 'No corresponde',
            'fecha_entrega_beneficio': self.today.isoformat(),
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AtencionSocial.objects.filter(caso=caso).count(), 3)

    def test_social_case_requires_valid_date_for_each_stage(self):
        self.client.force_login(self.supervisor)
        caso = CasoSocial.objects.create(
            codigo_caso='CASO-FECHAS-001',
            persona_anonimizada='PERSONA-ANON-FECHAS',
            delegacion=self.delegacion,
        )
        response = self.client.post('/casos-sociales/', {
            'action': 'registrar_atencion',
            'caso_id': str(caso.pk),
            'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
            'detalle': 'Diagnóstico inicial',
            'resultado': 'Visita pendiente',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(caso.atenciones.count(), 0)
        self.assertContains(response, 'La fecha de esta gestión es obligatoria')

        response = self.client.post('/casos-sociales/', {
            'action': 'registrar_atencion',
            'caso_id': str(caso.pk),
            'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
            'detalle': 'Diagnóstico inicial',
            'resultado': 'Visita pendiente',
            'fecha_gestion': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(caso.atenciones.count(), 0)
        self.assertContains(response, 'no puede ser anterior a hoy')

        response = self.client.post('/casos-sociales/', {
            'action': 'registrar_atencion',
            'caso_id': str(caso.pk),
            'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
            'detalle': 'Diagnóstico inicial',
            'resultado': 'Visita pendiente',
            'fecha_gestion': self.today.isoformat(),
        })
        self.assertRedirects(response, '/casos-sociales/')

        response = self.client.post('/casos-sociales/', {
            'action': 'registrar_atencion',
            'caso_id': str(caso.pk),
            'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
            'detalle': 'Visita al domicilio',
            'resultado': 'Atendida',
            'fecha_gestion': (self.today + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(caso.atenciones.count(), 1)
        self.assertContains(response, 'no puede ser futura')

    def test_catalogo_edit_preserves_service_snapshot_in_historical_activities(self):
        admin = Usuario.objects.create_user(
            username='admin.catalogo',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        create_response = self.client.post('/mantenedores/items/', {
            'action': 'guardar_catalogo',
            'codigo': 'SOC-01',
            'nombre': 'Atención social inicial',
            'area': 'DIDECO',
        })
        self.assertRedirects(create_response, '/mantenedores/items/')
        servicio = CatalogoServicio.objects.get(codigo='SOC-01')
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            catalogo_servicio=servicio,
            fecha_actividad=self.today,
        )
        self.assertEqual(actividad.catalogo_servicio_nombre, 'Atención social inicial')
        self.assertEqual(actividad.catalogo_servicio_area, 'DIDECO')

        update_response = self.client.post('/mantenedores/items/', {
            'action': 'guardar_catalogo',
            'catalogo_id': str(servicio.pk),
            'codigo': 'SOC-01',
            'nombre': 'Atención social actualizada',
            'area': 'DIDECO',
        })
        self.assertRedirects(update_response, '/mantenedores/items/')
        servicio.refresh_from_db()
        actividad.refresh_from_db()
        self.assertEqual(servicio.nombre, 'Atención social actualizada')
        self.assertEqual(actividad.catalogo_servicio_nombre, 'Atención social inicial')
        self.assertEqual(actividad.catalogo_servicio_area, 'DIDECO')

        deactivate_response = self.client.post('/mantenedores/items/', {
            'action': 'desactivar_catalogo',
            'catalogo_id': str(servicio.pk),
        })
        self.assertRedirects(deactivate_response, '/mantenedores/items/')
        servicio.refresh_from_db()
        self.assertFalse(servicio.activo)
        self.assertTrue(Actividad.objects.filter(pk=actividad.pk).exists())

    def test_activity_type_catalog_filters_by_area_and_preserves_snapshot(self):
        self.funcionario.area_entidad = self.area
        self.funcionario.save(update_fields=['area_entidad'])
        tipo_actual = TipoActividad.objects.create(
            area=self.area,
            nombre='Visita territorial',
        )
        otra_area = Area.objects.create(nombre='Otra área')
        TipoActividad.objects.create(area=otra_area, nombre='Atención clínica')

        response = self.client.get(
            '/actividad-diaria/items/',
            {'funcionario_id': self.funcionario.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [tipo['id'] for tipo in response.json()['tipos_actividad']],
            [tipo_actual.pk],
        )
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            item_medicion=self.item,
            tipo_actividad=tipo_actual,
            fecha_actividad=self.today,
        )
        tipo_actual.nombre = 'Visita comunitaria'
        tipo_actual.save(update_fields=['nombre'])
        actividad.refresh_from_db()

        self.assertEqual(actividad.tipo_actividad_nombre, 'Visita territorial')
        self.assertEqual(actividad.tipo_actividad_area, self.area.nombre)

    def test_activity_registration_requires_area_catalog_type_when_configured(self):
        self.funcionario.area_entidad = self.area
        self.funcionario.save(update_fields=['area_entidad'])
        tipo_actual = TipoActividad.objects.create(
            area=self.area,
            nombre='Atención en terreno',
        )
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(tipo_actividad_id=str(tipo_actual.pk)),
        )

        self.assertEqual(response.status_code, 302)
        actividad = Actividad.objects.get(funcionario=self.funcionario)
        self.assertEqual(actividad.tipo_actividad_id, tipo_actual.pk)
        self.assertEqual(actividad.tipo_actividad_nombre, 'Atención en terreno')
        self.assertEqual(actividad.tipo_actividad_area, self.area.nombre)

    def test_activity_api_rejects_type_from_another_area(self):
        self.funcionario.area_entidad = self.area
        self.funcionario.save(update_fields=['area_entidad'])
        otra_area = Area.objects.create(nombre='Área incompatible')
        tipo_incompatible = TipoActividad.objects.create(
            area=otra_area,
            nombre='Tipo incompatible',
        )
        serializer = ActividadSerializer(data={
            'funcionario': self.funcionario.pk,
            'delegacion': self.delegacion.pk,
            'contacto_telefono': '12345678',
            'tipo_actividad': tipo_incompatible.pk,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('tipo_actividad', serializer.errors)

    def test_social_attention_catalog_and_subattention_keep_historical_names(self):
        subatencion = SubatencionSocial.objects.create(
            tipo_atencion=self.tipo_atencion_catalogo,
            nombre='Visita domiciliaria',
        )
        caso = CasoSocial.objects.create(
            codigo_caso='CASO-CATALOGO-001',
            persona_anonimizada='PERSONA-CATALOGO',
            delegacion=self.delegacion,
        )
        self.client.force_login(self.supervisor)

        response = self.client.post('/casos-sociales/', {
            'action': 'registrar_atencion',
            'caso_id': str(caso.pk),
            'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
            'subatencion_catalogo_id': str(subatencion.pk),
            'detalle': 'Evaluación inicial',
            'resultado': 'Seguimiento acordado',
            'fecha_gestion': self.today.isoformat(),
        })

        self.assertRedirects(response, '/casos-sociales/')
        atencion = AtencionSocial.objects.get(caso=caso)
        self.assertEqual(atencion.tipo_atencion, 'Gestión social')
        self.assertEqual(atencion.tipo_atencion_area, self.area.nombre)
        self.assertEqual(atencion.subatencion, 'Visita domiciliaria')

        self.tipo_atencion_catalogo.nombre = 'Orientación social'
        self.tipo_atencion_catalogo.save(update_fields=['nombre'])
        subatencion.nombre = 'Visita de seguimiento'
        subatencion.save(update_fields=['nombre'])
        atencion.refresh_from_db()

        self.assertEqual(atencion.tipo_atencion, 'Gestión social')
        self.assertEqual(atencion.subatencion, 'Visita domiciliaria')

    def test_social_attention_api_can_use_catalog_without_legacy_text(self):
        caso = CasoSocial.objects.create(
            codigo_caso='CASO-API-CATALOGO-001',
            persona_anonimizada='PERSONA-API-CATALOGO',
            delegacion=self.delegacion,
        )
        serializer = AtencionSocialSerializer(data={
            'caso': caso.pk,
            'etapa': 1,
            'fecha_visita_programada': self.today.isoformat(),
            'tipo_atencion_catalogo': self.tipo_atencion_catalogo.pk,
            'detalle': 'Orientación registrada por API',
            'resultado': 'Seguimiento programado',
            'funcionario': self.supervisor.pk,
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        atencion = serializer.save(funcionario=self.supervisor)
        self.assertEqual(atencion.tipo_atencion, 'Gestión social')
        self.assertEqual(atencion.tipo_atencion_area, self.area.nombre)

    def test_social_attention_form_rejects_subattention_from_another_type(self):
        otro_tipo = TipoAtencionSocial.objects.create(
            area=self.area,
            nombre='Derivación',
        )
        subatencion = SubatencionSocial.objects.create(
            tipo_atencion=otro_tipo,
            nombre='Derivación externa',
        )
        form = AtencionSocialForm(
            {
                'tipo_atencion_catalogo_id': str(self.tipo_atencion_catalogo.pk),
                'subatencion_catalogo_id': str(subatencion.pk),
                'detalle': 'Detalle',
                'resultado': 'Resultado',
                'fecha_gestion': self.today.isoformat(),
            },
            etapa=1,
            area_id=self.area.pk,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('subatencion_catalogo_id', form.errors)

    def test_activity_catalog_can_be_managed_and_soft_deactivated(self):
        admin = Usuario.objects.create_user(
            username='catalog.admin',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(admin)

        response = self.client.post('/mantenedores/tipos-actividad/crear/', {
            'area': str(self.area.pk),
            'nombre': 'Operativo territorial',
            'descripcion': 'Actividad ejecutada en terreno.',
        })

        self.assertRedirects(response, '/mantenedores/tipos-actividad/')
        tipo = TipoActividad.objects.get(nombre='Operativo territorial')
        response = self.client.post(
            '/mantenedores/tipos-atencion-social/crear/',
            {
                'area': str(self.area.pk),
                'nombre': 'Orientación social',
                'descripcion': 'Orientación inicial.',
            },
        )
        self.assertRedirects(response, '/mantenedores/tipos-atencion-social/')
        tipo_atencion = TipoAtencionSocial.objects.get(nombre='Orientación social')
        response = self.client.post(
            '/mantenedores/subatenciones-sociales/crear/',
            {
                'tipo_atencion': str(tipo_atencion.pk),
                'nombre': 'Derivación externa',
                'descripcion': 'Derivación a otra unidad.',
            },
        )
        self.assertRedirects(response, '/mantenedores/subatenciones-sociales/')
        self.assertTrue(
            SubatencionSocial.objects.filter(
                tipo_atencion=tipo_atencion,
                nombre='Derivación externa',
            ).exists()
        )
        self.assertEqual(
            self.client.get('/mantenedores/tipos-atencion-social/').status_code,
            200,
        )
        self.assertEqual(
            self.client.get('/mantenedores/subatenciones-sociales/').status_code,
            200,
        )
        response = self.client.post(
            f'/mantenedores/tipos-actividad/{tipo.pk}/desactivar/',
        )
        self.assertRedirects(response, '/mantenedores/tipos-actividad/')
        tipo.refresh_from_db()
        self.assertFalse(tipo.activo)
        self.assertEqual(
            self.client.get('/mantenedores/tipos-actividad/').status_code,
            200,
        )

    def test_commitment_update_reassignment_and_state_are_audited(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Vecindario Centro',
            territorio='Sector Centro',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            area_apoyo='Operaciones y Territorio',
            descripcion='Descripción anterior',
            estado=CompromisoAgenda.Estado.PENDIENTE,
            observacion='Observación anterior',
        )
        reemplazo = Usuario.objects.create_user(
            username='funcionario.reasignado',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        delegacion_coordinadora = Delegacion.objects.create(
            nombre='Delegación coordinadora',
            codigo='COO',
        )
        coordinador = Usuario.objects.create_user(
            username='supervisor.interdelegacional',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            delegacion=delegacion_coordinadora,
        )
        self.client.force_login(coordinador)

        response = self.client.post(
            f'/tubo/{compromiso.pk}/editar/',
            {
                'solicitante': 'Vecindario Centro',
                'origen': 'EXT',
                'territorio': 'Sector Centro',
                'delegacion': str(self.delegacion.pk),
                'responsable': str(reemplazo.pk),
                'fecha_comprometida': self.today.isoformat(),
                'area_apoyo': 'Operaciones y Territorio',
                'descripcion': 'Trabajo finalizado',
                'estado': CompromisoAgenda.Estado.REALIZADO,
                'observacion': 'Trabajo verificado',
            },
        )

        self.assertRedirects(response, '/tubo/')
        compromiso.refresh_from_db()
        self.assertEqual(compromiso.responsable_id, reemplazo.pk)
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.REALIZADO)
        auditoria = RegistroAuditoria.objects.get(
            entidad='CompromisoAgenda',
            identificador_registro=str(compromiso.pk),
            accion='REASIGNAR_ACTUALIZAR_COMPROMISO',
        )
        self.assertEqual(auditoria.valor_anterior['responsable_id'], self.funcionario.pk)
        self.assertEqual(auditoria.valor_anterior['estado'], CompromisoAgenda.Estado.PENDIENTE)
        self.assertEqual(auditoria.valor_nuevo['responsable_id'], reemplazo.pk)
        self.assertEqual(auditoria.valor_nuevo['estado'], CompromisoAgenda.Estado.REALIZADO)
        observacion = ObservacionCompromiso.objects.get(compromiso=compromiso)
        self.assertEqual(observacion.texto, 'Trabajo verificado')
        self.assertEqual(observacion.compromiso_id, compromiso.pk)
        self.assertIsNotNone(observacion.creada_en)
        self.assertEqual(observacion.autor_id, coordinador.pk)
        detail_response = self.client.get(f'/tubo/{compromiso.pk}/editar/')
        self.assertContains(detail_response, 'Historial de observaciones')
        self.assertContains(detail_response, 'Trabajo verificado')
        self.assertContains(detail_response, coordinador.username)
        self.assertContains(
            detail_response,
            timezone.localtime(observacion.creada_en).strftime('%d/%m/%Y %H:%M'),
        )

    def test_commitment_update_rolls_back_if_audit_fails(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Vecindario Centro',
            territorio='Sector Centro',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            area_apoyo='Operaciones y Territorio',
            descripcion='Descripción anterior',
            estado=CompromisoAgenda.Estado.PENDIENTE,
        )
        reemplazo = Usuario.objects.create_user(
            username='funcionario.reasignado.rollback',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRATIVO,
            delegacion=self.delegacion,
        )
        self.client.force_login(self.supervisor)

        with patch(
            'sgr_app.web_views.RegistroAuditoria.objects.create',
            side_effect=IntegrityError('audit insert failed'),
        ):
            with self.assertRaises(IntegrityError):
                self.client.post(
                    f'/tubo/{compromiso.pk}/editar/',
                    {
                        'solicitante': 'Vecindario Centro',
                        'origen': 'EXT',
                        'territorio': 'Sector Centro',
                        'delegacion': str(self.delegacion.pk),
                        'responsable': str(reemplazo.pk),
                        'fecha_comprometida': self.today.isoformat(),
                        'area_apoyo': 'Operaciones y Territorio',
                        'descripcion': 'Cambio que debe revertirse',
                        'estado': CompromisoAgenda.Estado.REALIZADO,
                        'observacion': 'Debe revertirse',
                    },
                )

        compromiso.refresh_from_db()
        self.assertEqual(compromiso.responsable_id, self.funcionario.pk)
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.PENDIENTE)
        self.assertEqual(compromiso.descripcion, 'Descripción anterior')

    def test_quick_commitment_state_change_requires_observation_and_audits_snapshot(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Vecindario Centro',
            territorio='Sector Centro',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            estado=CompromisoAgenda.Estado.PENDIENTE,
        )
        self.client.force_login(self.supervisor)

        invalid_response = self.client.post(
            f'/tubo/{compromiso.pk}/cambiar-estado/',
            {'estado': CompromisoAgenda.Estado.EN_PROCESO, 'observacion': '  '},
        )
        self.assertRedirects(invalid_response, '/tubo/')
        compromiso.refresh_from_db()
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.PENDIENTE)
        self.assertFalse(RegistroAuditoria.objects.filter(
            accion='CAMBIO_ESTADO_COMPROMISO',
            identificador_registro=str(compromiso.pk),
        ).exists())

        response = self.client.post(
            f'/tubo/{compromiso.pk}/cambiar-estado/',
            {
                'estado': CompromisoAgenda.Estado.EN_PROCESO,
                'observacion': 'Equipo asignado a terreno',
            },
        )

        self.assertRedirects(response, '/tubo/')
        compromiso.refresh_from_db()
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.EN_PROCESO)
        audit = RegistroAuditoria.objects.get(
            accion='CAMBIO_ESTADO_COMPROMISO',
            identificador_registro=str(compromiso.pk),
        )
        self.assertEqual(audit.valor_anterior['estado'], CompromisoAgenda.Estado.PENDIENTE)
        self.assertEqual(audit.valor_nuevo['estado'], CompromisoAgenda.Estado.EN_PROCESO)
        self.assertEqual(audit.valor_nuevo['observacion'], 'Equipo asignado a terreno')
        self.assertEqual(audit.usuario_id, self.supervisor.pk)
        self.assertIsNotNone(audit.fecha_auditoria)
        historial = ObservacionCompromiso.objects.get(compromiso=compromiso)
        self.assertEqual(historial.autor_id, self.supervisor.pk)
        self.assertEqual(historial.texto, 'Equipo asignado a terreno')

    def test_quick_commitment_state_change_rolls_back_when_audit_fails(self):
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Vecindario Centro',
            territorio='Sector Centro',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            creado_por=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            estado=CompromisoAgenda.Estado.PENDIENTE,
        )
        self.client.force_login(self.supervisor)

        with patch(
            'sgr_app.web_views.RegistroAuditoria.objects.create',
            side_effect=IntegrityError('audit insert failed'),
        ):
            with self.assertRaises(IntegrityError):
                self.client.post(
                    f'/tubo/{compromiso.pk}/cambiar-estado/',
                    {
                        'estado': CompromisoAgenda.Estado.EN_PROCESO,
                        'observacion': 'Cambio que debe revertirse',
                    },
                )

        compromiso.refresh_from_db()
        self.assertEqual(compromiso.estado, CompromisoAgenda.Estado.PENDIENTE)

    @patch('urllib.request.urlopen')
    def test_reverse_geocoding_returns_nearest_address_for_map_coordinates(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(json.dumps({
            'display_name': 'Pasaje Los Huertos 123, La Serena, Chile',
            'address': {
                'road': 'Pasaje Los Huertos',
                'house_number': '123',
                'suburb': 'Las Compañías',
                'city': 'La Serena',
            },
        }).encode('utf-8'))

        response = self.client.get('/api/geocodificar/', {'lat': '-29.9', 'lng': '-71.2'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['direccion_encontrada'], 'Pasaje Los Huertos 123, Las Compañías, La Serena')
        self.assertTrue(response.json()['numero_encontrado'])
        self.assertTrue(response.json()['aproximada'])
        mock_urlopen.assert_called_once()

    def test_reverse_geocoding_rejects_out_of_range_coordinates(self):
        response = self.client.get('/api/geocodificar/', {'lat': '91', 'lng': '-71.2'})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['encontrado'])

    def test_deleting_commitment_removes_its_activity_and_evidence(self):
        coordinator = Usuario.objects.create_user(
            username='coordinador.compromiso.eliminar',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
        )
        self.client.force_login(coordinator)
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            fecha_actividad=self.today,
        )
        compromiso = CompromisoAgenda.objects.create(
            solicitante='Solicitante de prueba',
            delegacion=self.delegacion,
            responsable=self.funcionario,
            fecha_compromiso=self.today,
            fecha_comprometida=self.today,
            actividad=actividad,
        )
        Evidencia.objects.create(actividad=actividad)

        response = self.client.post(f'/tubo/{compromiso.id}/eliminar/')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CompromisoAgenda.objects.filter(pk=compromiso.id).exists())
        self.assertFalse(Actividad.objects.filter(pk=actividad.id).exists())
        self.assertFalse(Evidencia.objects.filter(actividad_id=actividad.id).exists())

    def test_rut_validation_checks_format_and_module_11(self):
        self.assertEqual(normalize_chilean_rut('11.111.111-1'), '11.111.111-1')
        self.assertEqual(normalize_chilean_rut('11111111-1'), '11.111.111-1')
        validate_chilean_rut('11.111.111-1')
        for invalid_rut in ('11.111111-1', '11.111.111-2', '111111111'):
            with self.subTest(invalid_rut=invalid_rut):
                with self.assertRaises(ValidationError):
                    normalize_chilean_rut(invalid_rut)
        for unformatted_rut in ('11111111-1', '12345678-5'):
            with self.subTest(unformatted_rut=unformatted_rut):
                with self.assertRaises(ValidationError):
                    validate_chilean_rut(unformatted_rut)

    def test_user_and_requester_store_normalized_rut_and_phone(self):
        user = Usuario.objects.create_user(
            username='usuario.normalizado',
            password='TestPassword123!',
            rut_anonimizado='11.111.111-1',
            telefono='9 1234 5678',
        )
        requester = Solicitante.objects.create(
            run='12.345.678-5',
            nombre='Persona',
            telefono='+56 9 1234 5678',
        )

        self.assertEqual(user.rut_anonimizado, '11.111.111-1')
        self.assertEqual(user.telefono, '+56912345678')
        self.assertEqual(requester.run, '12.345.678-5')
        self.assertEqual(requester.telefono, '+56912345678')

    def test_chilean_phone_validation_normalizes_mobile_numbers(self):
        self.assertEqual(normalize_chilean_phone('12345678'), '+56912345678')
        self.assertEqual(normalize_chilean_phone('9 1234 5678'), '+56912345678')
        self.assertEqual(normalize_chilean_phone('+56 9 1234 5678'), '+56912345678')
        for invalid_phone in (
            '+56 9 123 456',
            '56 9 1234 5678',
            '+56 2 2345 6789',
            '+56 71 234 5678',
            '+56 8 1234 5678',
            '9...1..2..3..4..5..6..7..8',
            '999999999',
            '99999999999999',
        ):
            with self.subTest(invalid_phone=invalid_phone):
                with self.assertRaises(ValidationError):
                    normalize_chilean_phone(invalid_phone)

    def test_user_serializer_validates_and_normalizes_rut_phone_and_email(self):
        invalid = UsuarioSerializer(data={
            'username': 'usuario.invalido',
            'rut_anonimizado': '11.111.111-2',
            'telefono': '555',
            'email': 'correo-invalido',
        })
        self.assertFalse(invalid.is_valid())
        self.assertIn('rut_anonimizado', invalid.errors)
        self.assertIn('telefono', invalid.errors)
        self.assertIn('email', invalid.errors)

        valid = UsuarioSerializer(data={
            'username': 'usuario.valido',
            'rut_anonimizado': '11.111.111-1',
            'telefono': '12345678',
            'email': 'persona@example.com',
        })
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.validated_data['rut_anonimizado'], '11.111.111-1')
        self.assertEqual(valid.validated_data['telefono'], '+56912345678')

        unformatted = UsuarioSerializer(data={
            'username': 'usuario.run.sin.formato',
            'rut_anonimizado': '11111111-1',
            'email': 'persona@example.com',
        })
        self.assertFalse(unformatted.is_valid())
        self.assertIn('rut_anonimizado', unformatted.errors)

    def test_activity_form_requires_a_registered_requester(self):
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(solicitante_id=''),
        )
        self.assertIn('activity_error=solicitante', response.url)
        self.assertFalse(Actividad.objects.exists())

    def test_activity_form_requires_contact_phone_and_preserves_it_after_validation_error(self):
        response = self.client.post(
            '/actividad-diaria/registrar-actividad/',
            self.activity_form(contacto_telefono=''),
        )

        self.assertIn('activity_error=telefono', response.url)
        self.assertFalse(Actividad.objects.exists())
        form_response = self.client.get(response.url)
        self.assertContains(form_response, 'teléfono de contacto chileno válido')
        self.assertContains(form_response, 'Teléfono de contacto:')

    def test_activity_serializer_normalizes_phone_and_rejects_invalid_phone(self):
        actividad = Actividad.objects.create(
            funcionario=self.funcionario,
            delegacion=self.delegacion,
            fecha_actividad=self.today,
        )
        valid = ActividadSerializer(
            actividad,
            data={'contacto_telefono': '12345678'},
            partial=True,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.validated_data['contacto_telefono'], '+56912345678')

        invalid = ActividadSerializer(
            actividad,
            data={'contacto_telefono': '12345'},
            partial=True,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn('contacto_telefono', invalid.errors)

        missing = ActividadSerializer(Actividad(funcionario=self.funcionario), data={})
        self.assertFalse(missing.is_valid())
        self.assertIn('contacto_telefono', missing.errors)

    def test_case_social_serializer_rejects_blank_required_fields(self):
        serializer = CasoSocialSerializer(data={
            'codigo_caso': '   ',
            'persona_anonimizada': ' ',
            'delegacion': self.delegacion.id,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('codigo_caso', serializer.errors)
        self.assertIn('persona_anonimizada', serializer.errors)

    def test_social_attention_serializer_requires_text_and_valid_stage(self):
        serializer = AtencionSocialSerializer(data={
            'caso': 999,
            'etapa': 9,
            'tipo_atencion': ' ',
            'detalle': ' ',
            'resultado': ' ',
            'funcionario': self.funcionario.id,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('etapa', serializer.errors)
        self.assertIn('tipo_atencion', serializer.errors)
        self.assertIn('detalle', serializer.errors)
        self.assertIn('resultado', serializer.errors)

    def test_superadmin_menu_link_is_not_available(self):
        self.client.force_login(self.funcionario)
        response = self.client.get('/personal/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Django Superadmin')

        superuser = Usuario.objects.create_superuser(
            username='admin.superuser',
            email='superuser@example.com',
            password='TestPassword123!',
        )
        self.client.force_login(superuser)
        response = self.client.get('/personal/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Django Superadmin')
        self.assertNotContains(response, 'href="/admin/"')


class ConcurrentActivityPersistenceTests(TransactionTestCase):
    def setUp(self):
        self.delegacion = Delegacion.objects.create(
            nombre='Delegación concurrente',
            codigo='CON',
        )
        self.funcionarios = [
            Usuario.objects.create_user(
                username=f'funcionario.concurrente.{index}',
                password='TestPassword123!',
                rol=Usuario.Rol.ADMINISTRATIVO,
                delegacion=self.delegacion,
            )
            for index in range(2)
        ]

    def test_simultaneous_distinct_activities_are_both_persisted(self):
        start = threading.Barrier(2)

        def create_activity(funcionario, description):
            close_old_connections()
            try:
                start.wait(timeout=5)
                with transaction.atomic():
                    return Actividad.objects.create(
                        funcionario=funcionario,
                        delegacion=self.delegacion,
                        solicitud_problema=description,
                    ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create_activity, funcionario, f'Registro {index}')
                for index, funcionario in enumerate(self.funcionarios, start=1)
            ]
            activity_ids = [future.result(timeout=10) for future in futures]

        self.assertEqual(len(set(activity_ids)), 2)
        activities = list(
            Actividad.objects.filter(pk__in=activity_ids)
            .order_by('solicitud_problema')
            .values_list('solicitud_problema', flat=True)
        )
        self.assertEqual(activities, ['Registro 1', 'Registro 2'])
