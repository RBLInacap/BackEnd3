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
