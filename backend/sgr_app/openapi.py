"""Ajustes del esquema OpenAPI (RNF-016)."""


def solo_api_v1(endpoints, **kwargs):
    """
    Documenta solo la versión estable /api/v1/. La ruta /api/ sin versión se
    mantiene por compatibilidad con la interfaz web, pero no forma parte del
    contrato publicado para integraciones.
    """
    return [endpoint for endpoint in endpoints if endpoint[0].startswith('/api/v1/')]
