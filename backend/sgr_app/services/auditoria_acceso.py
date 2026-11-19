"""Registro de accesos denegados (HU-26, CU-01/CU-02; OWASP A01/A09).

Cada respuesta 403 a un usuario autenticado, en la web o en la API, queda en la
auditoría inmutable con el usuario, la ruta, el método y la IP de origen.
"""
import logging

from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger('sgr.security')


def registrar_acceso_denegado(request, detalle=''):
    usuario = getattr(request, 'user', None)
    if not usuario or not usuario.is_authenticated:
        return
    from sgr_app.models import RegistroAuditoria
    from sgr_app.services.seguridad_acceso import obtener_ip_cliente

    ruta = request.get_full_path()[:500]
    logger.warning(
        'Acceso denegado: usuario=%r metodo=%s ruta=%s', usuario.get_username(), request.method, ruta,
    )
    try:
        RegistroAuditoria.objects.create(
            usuario=usuario,
            funcionario=usuario,
            evento='ACCESO_DENEGADO',
            accion='ACCESO_DENEGADO',
            entidad='Acceso',
            identificador=request.method[:50],
            identificador_registro=request.method[:50],
            valor_anterior=None,
            valor_nuevo={'ruta': ruta, 'metodo': request.method, 'detalle': str(detalle)[:300]},
            ip_origen=obtener_ip_cliente(request) or None,
        )
    except Exception:  # El registro nunca debe impedir responder el 403.
        logger.exception('No se pudo auditar el acceso denegado a %s', ruta)


def exception_handler_api(exc, context):
    """Manejador de errores de la API que además audita los 403."""
    response = drf_exception_handler(exc, context)
    if response is not None and response.status_code == 403:
        request = context.get('request')
        if request is not None:
            registrar_acceso_denegado(request, getattr(exc, 'detail', ''))
    return response
