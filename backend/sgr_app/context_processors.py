from django.utils import timezone
from sgr_app.models import PeriodoMedicion, Evidencia
from sgr_app.periodos import get_nombre_trimestre_actual

def sgr_global_context(request):
    try:
        periodo = PeriodoMedicion.objects.filter(cerrado=False).order_by('-fecha_inicio').first()
        if not periodo:
            periodo = PeriodoMedicion.objects.order_by('-fecha_inicio').first()
    except Exception:
        periodo = None

    # Conteo de evidencias pendientes de validación
    try:
        pendientes_count = Evidencia.objects.filter(estado=Evidencia.Estado.PENDIENTE).count()
    except Exception:
        pendientes_count = 0

    return {
        'periodo_activo': periodo,
        'nombre_periodo_activo': get_nombre_trimestre_actual(),
        'fecha_hoy_sistema': timezone.localtime(timezone.now()).strftime('%d/%m/%Y'),
        'total_pendientes_global': pendientes_count,
    }
