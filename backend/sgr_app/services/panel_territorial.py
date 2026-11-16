"""Indicadores de gestión territorial por delegación.

Responde a las funciones de las Delegaciones descritas en el documento de
contexto institucional: canalizar demandas, coordinar respuestas con las
unidades municipales y hacer seguimiento con plazo, estado y unidad
responsable. Todas las cifras se calculan con consultas agrupadas (RNF-002).
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, F, Q
from django.db.models.functions import Coalesce, TruncWeek
from django.utils import timezone

from sgr_app.models import Actividad, CompromisoAgenda

ESTADOS_REALIZADOS = (
    CompromisoAgenda.Estado.COMPLETADO,
    CompromisoAgenda.Estado.REALIZADO,
    CompromisoAgenda.Estado.CUMPLIDO,
)
SIN_CLASIFICAR = 'Sin clasificar'
MAX_PUNTOS_MAPA = 500


def _actividades(periodo, delegacion_ids):
    qs = Actividad.objects.filter(anulada=False)
    if periodo:
        qs = qs.filter(fecha_actividad__range=(periodo.fecha_inicio, periodo.fecha_termino))
    if delegacion_ids is not None:
        qs = qs.filter(delegacion_id__in=delegacion_ids)
    return qs


def _compromisos(periodo, delegacion_ids):
    qs = CompromisoAgenda.objects.all()
    if periodo:
        qs = qs.filter(fecha_comprometida__range=(periodo.fecha_inicio, periodo.fecha_termino))
    if delegacion_ids is not None:
        qs = qs.filter(delegacion_id__in=delegacion_ids)
    return qs


def _condiciones(hoy):
    realizado = Q(estado__in=ESTADOS_REALIZADOS)
    return {
        'realizado': realizado,
        'abierto': ~realizado,
        'vencido': ~realizado & Q(fecha_comprometida__lt=hoy),
        'fuera_plazo': realizado & Q(fecha_realizacion__date__gt=F('fecha_comprometida')),
    }


def _pct(parte, total):
    return round(parte / total * 100, 1) if total else None


def _completar(fila):
    """Agrega porcentajes derivados a una fila de conteos."""
    realizados = fila.get('realizados', 0)
    fila['en_plazo'] = realizados - fila.get('fuera_plazo', 0)
    fila['pct_en_plazo'] = _pct(fila['en_plazo'], realizados)
    fila['pct_vencidos'] = _pct(fila.get('vencidos', 0), fila.get('total', 0))
    return fila


def _dias_respuesta_por_delegacion(compromisos):
    """Promedio de días entre el registro y la realización, por delegación."""
    acumulado = defaultdict(lambda: [0.0, 0])
    filas = compromisos.filter(
        estado__in=ESTADOS_REALIZADOS, fecha_realizacion__isnull=False,
    ).values_list('delegacion_id', 'fecha_registro', 'fecha_realizacion')
    for delegacion_id, registro, realizacion in filas:
        dias = max((realizacion - registro).total_seconds() / 86400, 0)
        acumulado[delegacion_id][0] += dias
        acumulado[delegacion_id][1] += 1
    return {d: round(total / n, 1) for d, (total, n) in acumulado.items()}


def comparacion_territorial(periodo, delegaciones, hoy=None):
    """Una fila de indicadores por delegación (para supervisor y administrador)."""
    hoy = hoy or timezone.localdate()
    ids = [d.pk for d in delegaciones]
    cond = _condiciones(hoy)
    solicitudes = dict(
        _actividades(periodo, ids).values('delegacion_id').annotate(n=Count('id'))
        .values_list('delegacion_id', 'n')
    )
    compromisos = _compromisos(periodo, ids)
    conteos = {
        fila['delegacion_id']: fila
        for fila in compromisos.values('delegacion_id').annotate(
            total=Count('id'),
            realizados=Count('id', filter=cond['realizado']),
            abiertos=Count('id', filter=cond['abierto']),
            vencidos=Count('id', filter=cond['vencido']),
            fuera_plazo=Count('id', filter=cond['fuera_plazo']),
        )
    }
    dias = _dias_respuesta_por_delegacion(compromisos)

    filas = []
    for delegacion in delegaciones:
        fila = dict(conteos.get(delegacion.pk, {'total': 0, 'realizados': 0, 'abiertos': 0,
                                                'vencidos': 0, 'fuera_plazo': 0}))
        fila.update({
            'delegacion': delegacion,
            'solicitudes': solicitudes.get(delegacion.pk, 0),
            'dias_respuesta': dias.get(delegacion.pk),
        })
        filas.append(_completar(fila))
    # Primero las delegaciones que requieren más atención.
    filas.sort(key=lambda f: (-(f['vencidos']), -(f['abiertos']), f['delegacion'].nombre))
    return filas


def panel_territorial(periodo, delegacion, hoy=None):
    """Indicadores detallados de una delegación (Panel Territorial del delegado)."""
    hoy = hoy or timezone.localdate()
    resumen = comparacion_territorial(periodo, [delegacion], hoy)[0]
    cond = _condiciones(hoy)
    ids = [delegacion.pk]
    actividades = _actividades(periodo, ids)
    compromisos = _compromisos(periodo, ids)

    por_area = [
        _completar(fila) for fila in compromisos.values('area_apoyo').annotate(
            total=Count('id'),
            realizados=Count('id', filter=cond['realizado']),
            abiertos=Count('id', filter=cond['abierto']),
            vencidos=Count('id', filter=cond['vencido']),
            fuera_plazo=Count('id', filter=cond['fuera_plazo']),
        ).order_by('-vencidos', '-abiertos', 'area_apoyo')
    ]
    por_sector = list(
        compromisos.values('territorio').annotate(
            total=Count('id'), vencidos=Count('id', filter=cond['vencido']),
        ).order_by('-total', 'territorio')[:8]
    )
    problemas = list(
        actividades.annotate(
            tipo=Coalesce(
                F('catalogo_servicio__nombre'), F('tipo_actividad__nombre'), F('item_medicion__nombre_item'),
            ),
        ).values('tipo').annotate(total=Count('id')).order_by('-total')[:8]
    )
    for fila in problemas:
        fila['tipo'] = fila['tipo'] or SIN_CLASIFICAR

    hace_8_semanas = hoy - timedelta(weeks=8)
    tendencia = [
        {'semana': fila['semana'], 'total': fila['total']}
        for fila in actividades.filter(fecha_actividad__gte=hace_8_semanas)
        .annotate(semana=TruncWeek('fecha_actividad')).values('semana')
        .annotate(total=Count('id')).order_by('semana')
    ]
    maximo_semana = max((f['total'] for f in tendencia), default=0)
    for fila in tendencia:
        fila['pct'] = _pct(fila['total'], maximo_semana) or 0

    # Solo coordenadas, fecha y tipo: el mapa no expone datos personales.
    puntos = [
        {'lat': float(lat), 'lng': float(lng), 'fecha': fecha.isoformat()}
        for lat, lng, fecha in actividades.filter(latitud__isnull=False, longitud__isnull=False)
        .order_by('-fecha_actividad').values_list('latitud', 'longitud', 'fecha_actividad')[:MAX_PUNTOS_MAPA]
    ]

    proximos = list(
        compromisos.filter(cond['abierto'], fecha_comprometida__range=(hoy, hoy + timedelta(days=3)))
        .select_related('responsable').order_by('fecha_comprometida')[:10]
    )
    vencidos = list(
        compromisos.filter(cond['vencido']).select_related('responsable')
        .order_by('fecha_comprometida')[:10]
    )
    return {
        'resumen': resumen,
        'por_area': por_area,
        'por_sector': por_sector,
        'problemas': problemas,
        'tendencia': tendencia,
        'puntos_mapa': puntos,
        'proximos_vencer': proximos,
        'vencidos': vencidos,
    }


def proximos_vencimientos(usuario, dias=3, hoy=None):
    """Compromisos abiertos del funcionario que vencen en los próximos días."""
    hoy = hoy or timezone.localdate()
    abiertos = CompromisoAgenda.objects.filter(responsable=usuario).exclude(estado__in=ESTADOS_REALIZADOS)
    return {
        'hoy': hoy,
        'proximos': list(
            abiertos.filter(fecha_comprometida__range=(hoy, hoy + timedelta(days=dias)))
            .order_by('fecha_comprometida')[:10]
        ),
        'vencidos': abiertos.filter(fecha_comprometida__lt=hoy).count(),
    }
