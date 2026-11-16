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
