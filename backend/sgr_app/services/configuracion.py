"""
RF-038: vigencia histórica de la configuración de un período.

Un período cerrado se calcula con la configuración congelada en su versión de
cierre (metas, ponderadores, umbrales, tope, catálogos y versión de reglas), no
con los valores actuales de los modelos.
"""
from types import SimpleNamespace

from sgr_app.models import (
    CatalogoServicio,
    ItemMedicion,
    PeriodoMedicion,
    SubatencionSocial,
    TipoActividad,
    TipoAtencionSocial,
    VersionConfiguracionPeriodo,
)


# Versión de las reglas de cálculo vigentes. Si cambia alguna fórmula, se
# incrementa esta versión para que los snapshots registren qué reglas aplicaban.
REGLAS_CALCULO = {
    'version': '2026.11',
    'avance': 'Actividades no anuladas del período con evidencia aprobada '
              '(o compromisos cerrados con evidencia aprobada si el ítem mide compromisos).',
    'cumplimiento_item': 'avance / meta x 100',
    'aporte_ponderado': 'ponderador x min(cumplimiento, tope) / 100, redondeado a 2 decimales',
    'semaforo': 'avance ponderado / meta esperada al día x 100 comparado con umbrales verde y ámbar',
    'meta_esperada_dia': 'días transcurridos / días totales x 100, máximo 100',
}

PARAMETROS_PERIODO = (
    'umbral_semaforo_verde',
    'umbral_semaforo_ambar',
    'umbral_colectivo',
    'tope_cumplimiento',
)


def _snapshot_periodo(periodo):
    return {
        'nombre': periodo.nombre,
        'anio': periodo.anio,
        'trimestre': periodo.trimestre,
        'fecha_inicio': periodo.fecha_inicio.isoformat(),
        'fecha_termino': periodo.fecha_termino.isoformat(),
        'umbral_semaforo_verde': periodo.umbral_semaforo_verde,
        'umbral_semaforo_ambar': periodo.umbral_semaforo_ambar,
        'umbral_colectivo': periodo.umbral_colectivo,
        'tope_cumplimiento': periodo.tope_cumplimiento,
        'version_de_parametro': periodo.version_de_parametro,
        'version_cierre': periodo.version_cierre,
        'cerrado_en': periodo.cerrado_en.isoformat() if periodo.cerrado_en else None,
    }


def _snapshot_catalogos():
    return {
        'servicios': list(
            CatalogoServicio.objects.order_by('pk').values(
                'id', 'codigo', 'nombre', 'area_ref_id', 'area', 'activo',
            )
        ),
        'tipos_actividad': list(
            TipoActividad.objects.order_by('pk').values('id', 'area_id', 'nombre', 'activo')
        ),
        'tipos_atencion': list(
            TipoAtencionSocial.objects.order_by('pk').values('id', 'area_id', 'nombre', 'activo')
        ),
        'subatenciones': list(
            SubatencionSocial.objects.order_by('pk').values(
                'id', 'tipo_atencion_id', 'nombre', 'activo',
            )
        ),
    }


def snapshot_configuracion_periodo(periodo):
    return {
        'periodo': _snapshot_periodo(periodo),
        'items': list(
            ItemMedicion.objects.filter(periodo=periodo)
            .order_by('cargo_id', 'pk')
            .values(
                'id',
                'cargo_id',
                'nombre_item',
                'descripcion',
                'tipo_unidad',
                'meta_cuantitativa',
                'ponderador',
                'unidad_medida',
                'medir_compromisos',
                'activo',
            )
        ),
        'catalogos': _snapshot_catalogos(),
        'reglas_calculo': dict(REGLAS_CALCULO),
    }


def asegurar_version_cierre(periodo, usuario=None):
    """Garantiza que exista el snapshot de la versión con la que se cerró el período."""
    if not periodo.pk or not periodo.cerrado or not periodo.version_cierre:
        return None
    version = VersionConfiguracionPeriodo.objects.filter(
        periodo=periodo,
        version=periodo.version_cierre,
    ).first()
    if version:
        return version
    return VersionConfiguracionPeriodo.objects.create(
        periodo=periodo,
        version=periodo.version_cierre,
        configuracion=snapshot_configuracion_periodo(periodo),
        creado_por=usuario,
    )


def configuracion_congelada(periodo):
    """Devuelve el snapshot de cierre si el período está cerrado; si no, None."""
    if not getattr(periodo, 'pk', None) or not periodo.cerrado or not periodo.version_cierre:
        return None
    cache = getattr(periodo, '_configuracion_congelada_cache', None)
    if cache is not None and cache[0] == periodo.version_cierre:
        return cache[1]
    version = VersionConfiguracionPeriodo.objects.filter(
        periodo_id=periodo.pk,
        version=periodo.version_cierre,
    ).first()
    configuracion = version.configuracion if version else None
    periodo._configuracion_congelada_cache = (periodo.version_cierre, configuracion)
    return configuracion


def parametros_calculo(periodo):
    """Tope y umbrales que rigen el cálculo del período (congelados si está cerrado)."""
    parametros = {campo: getattr(periodo, campo) for campo in PARAMETROS_PERIODO}
    congelada = configuracion_congelada(periodo)
    if congelada:
        for campo in PARAMETROS_PERIODO:
            valor = congelada.get('periodo', {}).get(campo)
            if valor is not None:
                parametros[campo] = valor
    return SimpleNamespace(**parametros)


def items_calculo(periodo, cargo_id):
    """
    Ítems activos de un cargo para el cálculo del período.
    Si el período está cerrado se usan los ítems congelados en su versión de cierre.
    """
    if not getattr(periodo, 'pk', None) or not cargo_id:
        return []
    congelada = configuracion_congelada(periodo)
    if congelada and congelada.get('items') is not None:
        return [
            SimpleNamespace(
                id=item['id'],
                pk=item['id'],
                nombre_item=item['nombre_item'],
                descripcion=item.get('descripcion'),
                meta_cuantitativa=item['meta_cuantitativa'],
                ponderador=item['ponderador'],
                unidad_medida=item.get('unidad_medida', 'Actividades'),
                medir_compromisos=item.get('medir_compromisos', False),
            )
            for item in congelada['items']
            if item.get('cargo_id') == cargo_id and item.get('activo')
        ]
    return list(
        ItemMedicion.objects.filter(cargo_id=cargo_id, periodo=periodo, activo=True)
        .order_by('pk')
    )


__all__ = [
    'PeriodoMedicion',
    'REGLAS_CALCULO',
    'asegurar_version_cierre',
    'configuracion_congelada',
    'items_calculo',
    'parametros_calculo',
    'snapshot_configuracion_periodo',
]
