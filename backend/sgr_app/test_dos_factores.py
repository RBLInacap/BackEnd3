"""Pruebas de activación y obligatoriedad de la verificación en dos pasos (RNF-004)."""

import base64
import hashlib
import hmac
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from sgr_app.dos_factores import CLAVE_SESION, requiere_2fa_obligatorio
from sgr_app.models import RegistroAuditoria, Usuario

PASSWORD = 'TestPassword123!'
AHORA = 1_800_000_000  # instante fijo para que el código sea determinista


def totp(secreto, instante=AHORA):
    clave = base64.b32decode(secreto.upper() + '=' * (-len(secreto) % 8), casefold=True)
    digest = hmac.new(clave, (instante // 30).to_bytes(8, 'big'), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f'{(int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000:06d}'


@patch('sgr_app.web_views.time.time', return_value=AHORA)
class ActivacionDosFactoresTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(username='dosf.test', password=PASSWORD)
        self.client = Client()
        self.client.force_login(self.user)

    def test_muestra_qr_y_clave_manual(self, _t):
        response = self.client.get('/mi-cuenta/2fa/')
        self.assertContains(response, 'data:image/svg+xml;base64,')
        self.assertIn(CLAVE_SESION, self.client.session)

    def test_activa_con_codigo_correcto_y_audita(self, _t):
        self.client.get('/mi-cuenta/2fa/')
        secreto = self.client.session[CLAVE_SESION]

        response = self.client.post('/mi-cuenta/2fa/', {'accion': 'activar', 'codigo': totp(secreto)})

        self.assertRedirects(response, '/mi-ficha/', fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.requires_2fa)
        self.assertEqual(self.user.totp_secret, secreto)
        self.assertTrue(RegistroAuditoria.objects.filter(accion='ACTIVAR_2FA').exists())

    def test_no_activa_con_codigo_incorrecto(self, _t):
        self.client.get('/mi-cuenta/2fa/')
        self.client.post('/mi-cuenta/2fa/', {'accion': 'activar', 'codigo': '000000'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.requires_2fa)

    def test_login_posterior_pide_el_codigo(self, _t):
        self.client.get('/mi-cuenta/2fa/')
        secreto = self.client.session[CLAVE_SESION]
        self.client.post('/mi-cuenta/2fa/', {'accion': 'activar', 'codigo': totp(secreto)})

        nuevo = Client()
        response = nuevo.post('/login/', {'username': 'dosf.test', 'password': PASSWORD})
        self.assertTrue(response.context['show_2fa'])
        response = nuevo.post('/verificar-2fa/', {'code': totp(secreto)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(nuevo.session['_auth_user_id'], str(self.user.pk))

    def test_desactivar_requiere_codigo_valido(self, _t):
        self.user.totp_secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        self.user.requires_2fa = True
        self.user.save()

        self.client.post('/mi-cuenta/2fa/', {'accion': 'desactivar', 'codigo': '000000'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.requires_2fa)

        self.client.post('/mi-cuenta/2fa/', {'accion': 'desactivar', 'codigo': totp(self.user.totp_secret)})
        self.user.refresh_from_db()
        self.assertFalse(self.user.requires_2fa)
        self.assertIsNone(self.user.totp_secret)


@override_settings(TWO_FACTOR_REQUIRED_ROLES=['ADMINISTRADOR', 'COORDINADOR'])
class ObligatoriedadDosFactoresTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin.dosf', email='admin.dosf@example.com', password=PASSWORD,
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.funcionario = Usuario.objects.create_user(
            username='func.dosf', password=PASSWORD, rol=Usuario.Rol.FUNCIONARIO,
        )

    def test_rol_obligado_es_redirigido_a_activar(self):
        client = Client()
        client.force_login(self.admin)
        response = client.get('/dashboard/')
        self.assertRedirects(response, '/mi-cuenta/2fa/', fetch_redirect_response=False)

    def test_api_responde_403_hasta_activar(self):
        client = Client()
        client.force_login(self.admin)
        self.assertEqual(client.get('/api/actividades/').status_code, 403)

    def test_puede_cerrar_sesion_y_ver_la_pantalla_de_activacion(self):
        client = Client()
        client.force_login(self.admin)
        self.assertEqual(client.get('/mi-cuenta/2fa/').status_code, 200)
        self.assertEqual(client.post('/logout/').status_code, 302)

    def test_rol_no_obligado_navega_normalmente(self):
        self.assertFalse(requiere_2fa_obligatorio(self.funcionario))
        client = Client()
        client.force_login(self.funcionario)
        self.assertEqual(client.get('/personal/').status_code, 200)

    def test_con_2fa_activa_no_se_redirige_y_no_puede_desactivarla(self):
        self.admin.requires_2fa = True
        self.admin.totp_secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        self.admin.save()
        client = Client()
        client.force_login(self.admin)

        self.assertEqual(client.get('/dashboard/').status_code, 200)
        with patch('sgr_app.web_views.time.time', return_value=AHORA):
            client.post('/mi-cuenta/2fa/', {'accion': 'desactivar', 'codigo': totp(self.admin.totp_secret)})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.requires_2fa)
