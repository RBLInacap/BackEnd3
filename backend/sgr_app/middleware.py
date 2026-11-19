"""Cabecera Content-Security-Policy (OWASP A05 / mitigación de XSS)."""

from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """
    Añade la política CSP definida en settings.CONTENT_SECURITY_POLICY.

    Con CSP_REPORT_ONLY=True el navegador solo informa las violaciones en la
    consola (Content-Security-Policy-Report-Only) sin bloquear nada; sirve para
    validar la política antes de aplicarla.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        directivas = getattr(settings, 'CONTENT_SECURITY_POLICY', {}) or {}
        self.politica = '; '.join(
            f'{nombre} {" ".join(valores)}'.strip()
            for nombre, valores in directivas.items()
        )
        self.cabecera = (
            'Content-Security-Policy-Report-Only'
            if getattr(settings, 'CSP_REPORT_ONLY', False)
            else 'Content-Security-Policy'
        )

    def __call__(self, request):
        response = self.get_response(request)
        if self.politica and self.cabecera not in response:
            response[self.cabecera] = self.politica
        return response


class SoloLecturaConsultaMiddleware:
    """CU-38: el Usuario de consulta no puede modificar datos operativos.

    Cualquier escritura (POST, PUT, PATCH, DELETE) en la web o la API se rechaza
    con 403, salvo cerrar sesión y gestionar su propia contraseña y 2FA.
    """

    RUTAS_PERMITIDAS = (
        '/logout/',
        '/verificar-2fa/',
        '/mi-cuenta/2fa/',
        '/crear-contrasena/',
        '/password-reset/',
        '/api/auth/',
        '/api/v1/auth/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (
            request.method not in ('GET', 'HEAD', 'OPTIONS')
            and user is not None
            and user.is_authenticated
            and getattr(user, 'es_consulta', False)
            and not request.path.startswith(self.RUTAS_PERMITIDAS)
        ):
            from django.core.exceptions import PermissionDenied
            from django.http import HttpResponseForbidden

            from sgr_app.services.auditoria_acceso import registrar_acceso_denegado

            registrar_acceso_denegado(request, 'Usuario de consulta: solo lectura')
            return HttpResponseForbidden('Tu perfil es de solo consulta.')
        return self.get_response(request)


class AuditarAccesoDenegadoMiddleware:
    """Registra en la auditoría cada acceso denegado (403) de la web (HU-26).

    También deja disponible el usuario de la petición para los eventos que se
    registran desde los modelos (intento de alterar la auditoría, HU-30).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from sgr_app.services.contexto import fijar_usuario_actual, limpiar_usuario_actual

        token = fijar_usuario_actual(getattr(request, 'user', None))
        try:
            return self.get_response(request)
        finally:
            limpiar_usuario_actual(token)

    def process_exception(self, request, exception):
        from django.core.exceptions import PermissionDenied

        if isinstance(exception, PermissionDenied):
            from sgr_app.services.auditoria_acceso import registrar_acceso_denegado

            registrar_acceso_denegado(request, exception)
        return None
