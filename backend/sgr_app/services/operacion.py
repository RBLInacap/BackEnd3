"""Operación: alertas, registro de tareas automáticas y chequeo de salud.

RNF-001 (disponibilidad), RNF-010 (respaldo) y RNF-018 (monitoreo).
"""

import logging
import shutil
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.db import connection
from django.utils import timezone

logger = logging.getLogger('sgr.operacion')

# Tareas programadas y antigüedad máxima aceptable de su último éxito.
TAREAS_PROGRAMADAS = {
    'respaldar_base_datos': timedelta(hours=26),
    'purgar_evidencias_vencidas': timedelta(hours=26),
    'verificar_coherencia': timedelta(hours=26),
}


def enviar_alerta(asunto, mensaje):
    """Envía una alerta por correo a ALERTAS_EMAIL. Nunca lanza excepciones."""
    asunto = f'[SGR] {asunto}'
    logger.warning('%s: %s', asunto, mensaje)
    destinatarios = list(getattr(settings, 'ALERTAS_EMAIL', []) or [])
    if not destinatarios:
        logger.warning('ALERTAS_EMAIL no está configurado; la alerta solo quedó en el registro.')
        return False
    try:
        send_mail(asunto, mensaje, settings.DEFAULT_FROM_EMAIL, destinatarios)
        return True
    except Exception:  # El fallo del correo no debe detener el monitoreo.
        logger.exception('No fue posible enviar la alerta por correo.')
        return False


@contextmanager
def registrar_tarea(nombre):
    """
    Registra inicio, fin y resultado de una tarea automática.

    Si la tarea falla, guarda el error, envía una alerta y vuelve a lanzar la
    excepción para que systemd marque la ejecución como fallida.
    """
    from sgr_app.models import EjecucionTarea

    ejecucion = EjecucionTarea.objects.create(nombre=nombre)
    resultado = {'detalle': ''}
    try:
        yield resultado
    except Exception as exc:
        ejecucion.fin = timezone.now()
        ejecucion.exito = False
        ejecucion.detalle = f'{type(exc).__name__}: {exc}'[:4000]
        ejecucion.save(update_fields=['fin', 'exito', 'detalle'])
        enviar_alerta(f'Falló la tarea {nombre}', ejecucion.detalle)
        raise
    ejecucion.fin = timezone.now()
    ejecucion.exito = True
    ejecucion.detalle = str(resultado.get('detalle', ''))[:4000]
    ejecucion.save(update_fields=['fin', 'exito', 'detalle'])


def tareas_atrasadas(ahora=None):
    """Devuelve las tareas programadas cuyo último éxito es demasiado antiguo."""
    from sgr_app.models import EjecucionTarea

    ahora = ahora or timezone.now()
    atrasadas = []
    for nombre, limite in TAREAS_PROGRAMADAS.items():
        ultimo = (
            EjecucionTarea.objects.filter(nombre=nombre, exito=True)
            .order_by('-fin').values_list('fin', flat=True).first()
        )
        if ultimo is None or ahora - ultimo > limite:
            atrasadas.append((nombre, ultimo))
    return atrasadas


def _chequear_base_datos():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return True, 'ok'
    except Exception as exc:
        return False, type(exc).__name__


def _chequear_almacenamiento():
    if not getattr(settings, 'USE_S3', False):
        return True, 'local'
    from sgr_app.services.aws_service import get_s3_client

    try:
        get_s3_client().head_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME)
        return True, 'ok'
    except Exception as exc:
        return False, type(exc).__name__


def uso_disco_pct(ruta=None):
    uso = shutil.disk_usage(ruta or settings.BASE_DIR)
    return round(uso.used / uso.total * 100, 1)


def memoria_disponible_pct():
    """Porcentaje de memoria disponible (solo Linux); None si no se puede medir."""
    try:
        datos = {}
        for linea in Path('/proc/meminfo').read_text().splitlines():
            clave, valor = linea.split(':', 1)
            datos[clave] = int(valor.split()[0])
        return round(datos['MemAvailable'] / datos['MemTotal'] * 100, 1)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return None


def estado_salud(profundo=False):
    """
    Chequeo de dependencias. `profundo` incluye S3 (más lento; lo usa el monitor).
    Devuelve (ok, detalle) sin exponer información sensible.
    """
    checks = {}
    ok_bd, checks['base_datos'] = _chequear_base_datos()
    ok = ok_bd
    if profundo:
        ok_s3, checks['almacenamiento'] = _chequear_almacenamiento()
        ok = ok and ok_s3
    disco = uso_disco_pct()
    checks['disco'] = 'ok' if disco < 98 else 'lleno'
    ok = ok and disco < 98
    return ok, checks
