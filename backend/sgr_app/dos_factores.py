"""Verificación en dos pasos (TOTP) - activación por el usuario y obligatoriedad por rol (RNF-004).

Compatible con Google Authenticator, Microsoft Authenticator, Authy, etc.
"""

import base64
import secrets
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from sgr_app.models import RegistroAuditoria
from sgr_app.services import seguridad_acceso
from sgr_app.services.seguridad_acceso import obtener_ip_cliente

EMISOR = 'SGR La Serena'
CLAVE_SESION = 'dos_factores_secreto_pendiente'


def generar_secreto():
    """Secreto aleatorio de 160 bits en base32 (recomendación RFC 4226)."""
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def uri_otpauth(usuario, secreto):
    cuenta = quote(f'{EMISOR}:{usuario.get_username()}')
    return f'otpauth://totp/{cuenta}?secret={secreto}&issuer={quote(EMISOR)}&digits=6&period=30'


def qr_data_uri(texto):
    """QR en SVG como data URI (sin HTML marcado como seguro)."""
    imagen = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return 'data:image/svg+xml;base64,' + base64.b64encode(imagen.to_string()).decode('ascii')


def roles_con_2fa_obligatorio():
    return {r.strip().upper() for r in settings.TWO_FACTOR_REQUIRED_ROLES if r.strip()}


def requiere_2fa_obligatorio(usuario):
    """True si el rol del usuario exige verificación en dos pasos."""
    roles = roles_con_2fa_obligatorio()
    if not roles or not getattr(usuario, 'is_authenticated', False):
        return False
    if (usuario.rol or '').upper() in roles:
        return True
    if 'ADMINISTRADOR' in roles and usuario.es_admin():
        return True
    return 'SUPERVISOR' in roles and usuario.es_supervisor


def _auditar(request, accion):
    RegistroAuditoria.objects.create(
        usuario=request.user,
        evento='MODIFICACION',
        accion=accion,
        entidad='Usuario',
        identificador=str(request.user.pk),
        valor_nuevo={'requires_2fa': request.user.requires_2fa},
        ip_origen=obtener_ip_cliente(request) or None,
    )


@never_cache
def configurar_2fa(request):
    from sgr_app.web_views import codigo_totp_valido, es_codigo_2fa_valido

    usuario = request.user
    obligatorio = requiere_2fa_obligatorio(usuario)

    if request.method == 'POST':
        accion = request.POST.get('accion')
        codigo = request.POST.get('codigo', '').strip()

        if accion == 'activar' and not usuario.requires_2fa:
            secreto = request.session.get(CLAVE_SESION)
            if secreto and codigo_totp_valido(secreto, codigo):
                usuario.totp_secret = secreto
                usuario.requires_2fa = True
                usuario.save(update_fields=['totp_secret', 'requires_2fa'])
                request.session.pop(CLAVE_SESION, None)
                _auditar(request, 'ACTIVAR_2FA')
                messages.success(request, 'Verificación en dos pasos activada. Se pedirá el código en cada inicio de sesión.')
                return redirect('web_mi_ficha')
            messages.error(request, 'El código no es correcto. Revisa la hora del teléfono e inténtalo de nuevo.')

        elif accion == 'desactivar' and usuario.requires_2fa:
            if obligatorio:
                messages.error(request, 'Tu rol exige verificación en dos pasos; no se puede desactivar.')
            elif seguridad_acceso.two_factor_bloqueado(usuario):
                messages.error(request, seguridad_acceso.mensaje_bloqueo())
            elif es_codigo_2fa_valido(usuario, codigo):
                seguridad_acceso.registrar_2fa_exitoso(usuario)
                usuario.requires_2fa = False
                usuario.totp_secret = None
                usuario.save(update_fields=['totp_secret', 'requires_2fa'])
                _auditar(request, 'DESACTIVAR_2FA')
                messages.success(request, 'Verificación en dos pasos desactivada.')
                return redirect('web_mi_ficha')
            else:
                seguridad_acceso.registrar_2fa_fallido(request, usuario)
                messages.error(request, 'El código no es correcto.')
        return redirect('web_configurar_2fa')

    contexto = {'activo': usuario.requires_2fa, 'obligatorio': obligatorio}
    if not usuario.requires_2fa:
        secreto = request.session.get(CLAVE_SESION) or generar_secreto()
        request.session[CLAVE_SESION] = secreto
        contexto.update({
            'secreto': ' '.join(secreto[i:i + 4] for i in range(0, len(secreto), 4)),
            'qr': qr_data_uri(uri_otpauth(usuario, secreto)),
        })
    return render(request, 'dos_factores.html', contexto)


class Exigir2FAMiddleware:
    """
    Si el rol del usuario exige 2FA y aún no la activó, solo puede acceder a la
    pantalla de activación y a cerrar sesión (RNF-004).
    """

    RUTAS_PERMITIDAS = ('/mi-cuenta/2fa/', '/logout/', '/static/', '/health/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if (
            usuario is not None
            and usuario.is_authenticated
            and not usuario.requires_2fa
            and not request.path.startswith(self.RUTAS_PERMITIDAS)
            and requiere_2fa_obligatorio(usuario)
        ):
            if request.path.startswith('/api/'):
                return JsonResponse(
                    {'detail': 'Debes activar la verificación en dos pasos antes de usar el sistema.'},
                    status=403,
                )
            messages.info(request, 'Por seguridad, tu rol requiere activar la verificación en dos pasos.')
            return redirect(reverse('web_configurar_2fa'))
        return self.get_response(request)
