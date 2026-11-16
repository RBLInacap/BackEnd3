"""Limitación de intentos de autenticación y registro de eventos de seguridad.

Controles OWASP A07 (fallos de autenticación) y A09 (registro y monitoreo):
- Bloqueo temporal tras N intentos fallidos por usuario+IP y por IP.
- Bloqueo temporal tras N códigos 2FA incorrectos por usuario.
- Los contadores se guardan en base de datos para que funcionen con varios
  workers de Gunicorn y sobrevivan a reinicios.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

security_logger = logging.getLogger('sgr.security')


def obtener_ip_cliente(request):
    """
    Devuelve la IP del cliente.

    Detrás de proxies (ALB + Nginx) REMOTE_ADDR es la IP del proxy, por lo que
    se usa X-Forwarded-For solo si TRUSTED_PROXY_COUNT > 0. Se toma la entrada
    añadida por el proxy de confianza más externo (misma convención que
    NUM_PROXIES de Django REST Framework), que el cliente no puede falsificar.
    """
    if request is None:
        return ''
    remote_addr = request.META.get('REMOTE_ADDR', '') or ''
    proxies = getattr(settings, 'TRUSTED_PROXY_COUNT', 0)
    if proxies > 0:
        forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
        hops = [hop.strip() for hop in forwarded_for.split(',') if hop.strip()]
        if hops:
            return hops[-min(proxies, len(hops))]
    return remote_addr


def _hash_clave(*partes):
    texto = '|'.join(str(parte) for parte in partes)
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class _Ambito:
    clave: str
    limite: int


def _ambitos_login(username, ip):
    usuario = str(username or '').strip().lower()
    return [
        _Ambito(_hash_clave('login-usuario-ip', usuario, ip), settings.LOGIN_MAX_FAILED_ATTEMPTS),
        _Ambito(_hash_clave('login-ip', ip), settings.LOGIN_MAX_FAILED_ATTEMPTS_PER_IP),
    ]


def _ambitos_2fa(user_id):
    return [_Ambito(_hash_clave('2fa-usuario', user_id), settings.TWO_FACTOR_MAX_FAILED_ATTEMPTS)]


def _ventana():
    return timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)


def _esta_bloqueado(ambitos):
    from sgr_app.models import BloqueoAcceso

    ahora = timezone.now()
    return BloqueoAcceso.objects.filter(
        clave__in=[ambito.clave for ambito in ambitos],
        bloqueado_hasta__gt=ahora,
    ).exists()


def _registrar_fallo(ambitos):
    """Incrementa los contadores; devuelve True si algún ámbito quedó bloqueado."""
    from sgr_app.models import BloqueoAcceso

    ahora = timezone.now()
    bloqueado = False
    for ambito in ambitos:
        with transaction.atomic():
            # get_or_create resuelve la carrera de inserción con un savepoint interno.
            registro, _ = BloqueoAcceso.objects.select_for_update().get_or_create(
                clave=ambito.clave,
                defaults={'primer_fallo': ahora},
            )

            ventana_vencida = registro.primer_fallo <= ahora - _ventana()
            bloqueo_vencido = registro.bloqueado_hasta and registro.bloqueado_hasta <= ahora
            if ventana_vencida or bloqueo_vencido:
                registro.intentos_fallidos = 0
                registro.primer_fallo = ahora
                registro.bloqueado_hasta = None

            registro.intentos_fallidos += 1
            if registro.intentos_fallidos >= ambito.limite:
                registro.bloqueado_hasta = ahora + _ventana()
                bloqueado = True
            registro.save()
    return bloqueado


def _limpiar(ambitos):
    from sgr_app.models import BloqueoAcceso

    BloqueoAcceso.objects.filter(clave__in=[ambito.clave for ambito in ambitos]).delete()


# --- API pública: inicio de sesión -------------------------------------------

def login_bloqueado(request, username):
    return _esta_bloqueado(_ambitos_login(username, obtener_ip_cliente(request)))


def registrar_login_fallido(request, username):
    ip = obtener_ip_cliente(request)
    if _registrar_fallo(_ambitos_login(username, ip)):
        security_logger.warning(
            'Bloqueo temporal de inicio de sesión: usuario=%r ip=%s',
            (username or '')[:150], ip,
        )


def registrar_login_exitoso(request, username):
    """Reinicia el contador usuario+IP; el contador por IP se mantiene."""
    ip = obtener_ip_cliente(request)
    _limpiar(_ambitos_login(username, ip)[:1])


# --- API pública: segundo factor ---------------------------------------------

def two_factor_bloqueado(user):
    return _esta_bloqueado(_ambitos_2fa(user.pk))


def registrar_2fa_fallido(request, user):
    security_logger.warning(
        'Código 2FA incorrecto: usuario=%s ip=%s', user.pk, obtener_ip_cliente(request),
    )
    if _registrar_fallo(_ambitos_2fa(user.pk)):
        security_logger.warning(
            'Bloqueo temporal de 2FA: usuario=%s ip=%s', user.pk, obtener_ip_cliente(request),
        )


def registrar_2fa_exitoso(user):
    _limpiar(_ambitos_2fa(user.pk))


MENSAJE_BLOQUEO = (
    'Demasiados intentos fallidos. Por seguridad, el acceso quedó bloqueado '
    'temporalmente. Intente nuevamente en {minutos} minutos.'
)


def mensaje_bloqueo():
    return MENSAJE_BLOQUEO.format(minutos=settings.LOGIN_LOCKOUT_MINUTES)
