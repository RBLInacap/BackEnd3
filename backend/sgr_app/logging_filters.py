import logging


class ExcluirHealthCheck(logging.Filter):
    """
    Evita que cada 503 de /health/ dispare un correo de error: durante una caída
    el monitor consulta cada minuto y ya envía sus propias alertas (caída y
    recuperación), sin repetir.
    """

    def filter(self, record):
        request = getattr(record, 'request', None)
        return not (request is not None and getattr(request, 'path', '').startswith('/health/'))


class SinTracebackEnErroresCliente(logging.Filter):
    """
    Django adjunta el traceback de PermissionDenied/Http404 a cada 403/404, lo que
    llena el registro con trazas de rechazos esperados. Se conserva la línea de
    advertencia (ruta y código) y se quita la traza; los 5xx la mantienen.
    """

    def filter(self, record):
        status_code = getattr(record, 'status_code', None)
        if status_code is not None and status_code < 500:
            record.exc_info = None
            record.exc_text = None
        return True
