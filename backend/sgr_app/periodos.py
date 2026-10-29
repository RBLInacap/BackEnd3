from datetime import date, timedelta

from django.utils import timezone
from sgr_app.models import PeriodoMedicion


TRIMESTRE_MESES = (
    ('Enero', 'Marzo'),
    ('Abril', 'Junio'),
    ('Julio', 'Septiembre'),
    ('Octubre', 'Diciembre'),
)


def get_nombre_trimestre_actual(fecha=None):
    fecha = fecha or timezone.localdate()
    numero_trimestre = (fecha.month - 1) // 3
    mes_inicio, mes_termino = TRIMESTRE_MESES[numero_trimestre]
    return f"{numero_trimestre + 1}° Trimestre ({mes_inicio} - {mes_termino}) - {fecha.year}"


def get_periodo_trimestre_actual(fecha=None):
    fecha = fecha or timezone.localdate()
    numero_trimestre = (fecha.month - 1) // 3
    fecha_inicio = date(fecha.year, numero_trimestre * 3 + 1, 1)
    siguiente_mes = fecha_inicio.month + 3
    fecha_siguiente = (
        date(fecha.year + 1, 1, 1)
        if siguiente_mes > 12
        else date(fecha.year, siguiente_mes, 1)
    )
    fecha_termino = fecha_siguiente - timedelta(days=1)

    periodo = PeriodoMedicion.objects.filter(
        fecha_inicio=fecha_inicio,
        fecha_termino=fecha_termino,
    ).order_by('-id').first()
    if periodo:
        return periodo, True

    return PeriodoMedicion(
        nombre=get_nombre_trimestre_actual(fecha),
        fecha_inicio=fecha_inicio,
        fecha_termino=fecha_termino,
        dias_computables=(fecha_termino - fecha_inicio).days + 1,
    ), False
