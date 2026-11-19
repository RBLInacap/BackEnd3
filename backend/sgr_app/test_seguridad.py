"""Pruebas de los controles de seguridad (OWASP A05, A07, A09)."""

import base64
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from sgr_app.models import BloqueoAcceso, Usuario
from sgr_app.services.seguridad_acceso import obtener_ip_cliente

PASSWORD = 'Password123!'
TOTP_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'


@override_settings(
    LOGIN_MAX_FAILED_ATTEMPTS=5,
    LOGIN_MAX_FAILED_ATTEMPTS_PER_IP=30,
    TWO_FACTOR_MAX_FAILED_ATTEMPTS=5,
    LOGIN_LOCKOUT_MINUTES=15,
    TRUSTED_PROXY_COUNT=0,
)
class BloqueoInicioSesionTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(username='seguridad.test', password=PASSWORD)

    def _login(self, client, password):
        return client.post('/login/', {'username': 'seguridad.test', 'password': password})

    def test_bloquea_tras_cinco_intentos_fallidos_aunque_luego_use_clave_correcta(self):
        client = Client()
        for _ in range(5):
            self.assertContains(self._login(client, 'incorrecta'), 'Credenciales inválidas')

        response = self._login(client, PASSWORD)

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Demasiados intentos fallidos', status_code=429)
        self.assertNotIn('_auth_user_id', client.session)

    def test_login_exitoso_reinicia_el_contador(self):
        client = Client()
        for _ in range(4):
            self._login(client, 'incorrecta')
        self.assertRedirects(self._login(client, PASSWORD), '/dashboard/', fetch_redirect_response=False)
        client.post('/logout/')

        for _ in range(4):
            self._login(client, 'incorrecta')
        self.assertRedirects(self._login(client, PASSWORD), '/dashboard/', fetch_redirect_response=False)

    def test_bloqueo_vence_despues_del_tiempo_configurado(self):
        client = Client()
        for _ in range(5):
            self._login(client, 'incorrecta')
        BloqueoAcceso.objects.update(bloqueado_hasta=timezone.now() - timedelta(seconds=1))

        self.assertRedirects(self._login(client, PASSWORD), '/dashboard/', fetch_redirect_response=False)

    def test_bloqueo_por_usuario_no_afecta_a_otra_cuenta(self):
        Usuario.objects.create_user(username='otra.cuenta', password=PASSWORD)
        client = Client()
        for _ in range(5):
            self._login(client, 'incorrecta')

        response = client.post('/login/', {'username': 'otra.cuenta', 'password': PASSWORD})

        self.assertRedirects(response, '/dashboard/', fetch_redirect_response=False)

    @override_settings(LOGIN_MAX_FAILED_ATTEMPTS_PER_IP=3)
    def test_bloqueo_por_ip_frena_ataques_contra_varias_cuentas(self):
        client = Client()
        for nombre in ('a', 'b', 'c'):
            client.post('/login/', {'username': nombre, 'password': 'x'})

        self.assertEqual(self._login(client, PASSWORD).status_code, 429)

    def test_no_guarda_usuario_ni_ip_en_texto_plano(self):
        self._login(Client(), 'incorrecta')

        for clave in BloqueoAcceso.objects.values_list('clave', flat=True):
            self.assertRegex(clave, r'^[0-9a-f]{64}$')
            self.assertNotIn('seguridad', clave)

    def test_api_login_tambien_se_bloquea(self):
        client = Client()
        for _ in range(5):
            response = client.post(
                '/api/auth/login/',
                {'username': 'seguridad.test', 'password': 'incorrecta'},
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 401)

        response = client.post(
            '/api/auth/login/',
            {'username': 'seguridad.test', 'password': PASSWORD},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 429)


class EnumeracionUsuariosTests(TestCase):
    def test_cuenta_inactiva_con_clave_incorrecta_no_revela_su_estado(self):
        Usuario.objects.create_user(username='inactivo.test', password=PASSWORD, is_active=False)

        response = Client().post('/login/', {'username': 'inactivo.test', 'password': 'incorrecta'})

        self.assertContains(response, 'Credenciales inválidas')
        self.assertNotContains(response, 'bloqueado o inactivo')

    def test_clave_incorrecta_no_desactiva_cuenta_con_activacion_vencida(self):
        user = Usuario.objects.create_user(username='vencido.test', password=PASSWORD)
        user.must_change_password = True
        user.initial_login_expires_at = timezone.now() - timedelta(seconds=1)
        user.save(update_fields=['must_change_password', 'initial_login_expires_at'])

        response = Client().post('/login/', {'username': 'vencido.test', 'password': 'incorrecta'})

        self.assertContains(response, 'Credenciales inválidas')
        user.refresh_from_db()
        self.assertTrue(user.is_active)


@override_settings(TWO_FACTOR_MAX_FAILED_ATTEMPTS=5, LOGIN_LOCKOUT_MINUTES=15)
@patch('sgr_app.web_views.time.time', return_value=59)
class BloqueoSegundoFactorTests(TestCase):
    """Con time=59 y este secreto, el único código TOTP válido es 287082."""

    def setUp(self):
        self.user = Usuario.objects.create_user(username='dosfa.test', password=PASSWORD)
        self.user.requires_2fa = True
        self.user.totp_secret = TOTP_SECRET
        self.user.save(update_fields=['requires_2fa', 'totp_secret'])

    def _iniciar(self, client):
        client.post('/login/', {'username': 'dosfa.test', 'password': PASSWORD})

    def test_bloquea_2fa_tras_cinco_codigos_incorrectos(self, _mock_time):
        client = Client()
        self._iniciar(client)
        for _ in range(5):
            client.post('/verificar-2fa/', {'code': '000000'})

        response = client.post('/verificar-2fa/', {'code': '287082'})

        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertNotIn('pending_2fa_user_id', client.session)

    def test_codigo_correcto_antes_del_limite_inicia_sesion(self, _mock_time):
        client = Client()
        self._iniciar(client)
        for _ in range(4):
            client.post('/verificar-2fa/', {'code': '000000'})

        response = client.post('/verificar-2fa/', {'code': '287082'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session.get('_auth_user_id'), str(self.user.pk))
        # Normativa: el registro no se borra; se reinicia el contador y se levanta el bloqueo.
        self.assertFalse(
            BloqueoAcceso.objects.filter(intentos_fallidos__gt=0).exists()
            or BloqueoAcceso.objects.filter(bloqueado_hasta__isnull=False).exists()
        )

    def test_api_2fa_tambien_se_bloquea(self, _mock_time):
        client = Client()
        client.post(
            '/api/auth/login/',
            {'username': 'dosfa.test', 'password': PASSWORD},
            content_type='application/json',
        )
        for _ in range(5):
            client.post('/api/auth/verificar-2fa/', {'code': '000000'}, content_type='application/json')

        response = client.post(
            '/api/auth/verificar-2fa/', {'code': '287082'}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 429)


class ApiSinBasicAuthTests(TestCase):
    def test_api_rechaza_credenciales_basic(self):
        Usuario.objects.create_user(username='basic.test', password=PASSWORD)
        token = base64.b64encode(f'basic.test:{PASSWORD}'.encode()).decode()

        response = Client().get('/api/usuarios/', HTTP_AUTHORIZATION=f'Basic {token}')

        self.assertIn(response.status_code, (401, 403))


class CabecerasSeguridadTests(TestCase):
    @override_settings(CSP_REPORT_ONLY=True)
    def test_csp_en_modo_informe(self):
        response = Client().get('/login/')

        politica = response['Content-Security-Policy-Report-Only']
        self.assertIn("default-src 'self'", politica)
        self.assertIn("frame-ancestors 'none'", politica)
        self.assertNotIn('Content-Security-Policy', response.headers)

    @override_settings(CSP_REPORT_ONLY=False)
    def test_csp_aplicada(self):
        response = Client().get('/login/')

        self.assertIn("object-src 'none'", response['Content-Security-Policy'])

    def test_cabeceras_basicas(self):
        response = Client().get('/login/')

        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')


class IpClienteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_sin_proxies_ignora_x_forwarded_for(self):
        request = self.factory.get('/', REMOTE_ADDR='10.0.0.5', HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(obtener_ip_cliente(request), '10.0.0.5')

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_con_alb_y_nginx_toma_la_ip_agregada_por_el_alb(self):
        # El cliente falsifica 6.6.6.6; el ALB agrega la IP real y Nginx la del ALB.
        request = self.factory.get(
            '/',
            REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_FOR='6.6.6.6, 200.1.2.3, 172.31.0.10',
        )
        self.assertEqual(obtener_ip_cliente(request), '200.1.2.3')


class RegistroSeguridadTests(TestCase):
    def test_login_fallido_queda_registrado_sin_la_contrasena(self):
        with self.assertLogs('sgr.security', level='WARNING') as logs:
            Client().post('/login/', {'username': 'nadie', 'password': 'SecretoNoLoguear1!'})

        salida = '\n'.join(logs.output)
        self.assertIn('Inicio de sesión fallido', salida)
        self.assertNotIn('SecretoNoLoguear1!', salida)
