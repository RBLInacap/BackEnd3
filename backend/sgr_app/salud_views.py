"""Endpoint de salud para el monitor de disponibilidad (RNF-001 / RNF-018)."""

from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from sgr_app.services.operacion import estado_salud


@login_not_required
@never_cache
@require_GET
def health(request):
    """
    200 si Django y la base de datos responden; 503 si alguna dependencia falla.

    Con ?profundo=1 también comprueba S3. Solo expone estados, nunca errores
    detallados, rutas ni versiones.
    """
    ok, checks = estado_salud(profundo=request.GET.get('profundo') == '1')
    return JsonResponse(
        {'estado': 'ok' if ok else 'error', 'checks': checks},
        status=200 if ok else 503,
    )
