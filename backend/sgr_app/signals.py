"""Registro técnico de eventos de autenticación (OWASP A09).

Nunca se registran contraseñas, códigos 2FA ni el contenido de formularios.
"""

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from sgr_app.services.seguridad_acceso import obtener_ip_cliente, security_logger


@receiver(user_logged_in)
def registrar_inicio_sesion(sender, request, user, **kwargs):
    security_logger.info(
        'Inicio de sesión: usuario=%s ip=%s', user.pk, obtener_ip_cliente(request),
    )


@receiver(user_logged_out)
def registrar_cierre_sesion(sender, request, user, **kwargs):
    security_logger.info(
        'Cierre de sesión: usuario=%s ip=%s',
        getattr(user, 'pk', None), obtener_ip_cliente(request),
    )


@receiver(user_login_failed)
def registrar_login_fallido(sender, credentials, request=None, **kwargs):
    # Django ya enmascara la contraseña en `credentials`; solo se usa el usuario.
    security_logger.warning(
        'Inicio de sesión fallido: usuario=%r ip=%s',
        str(credentials.get('username', ''))[:150], obtener_ip_cliente(request),
    )
