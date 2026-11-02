from datetime import date
from django.utils import timezone
from django.db.models import Count, Q
from sgr_app.models import (
    Actividad,
    AjusteIncentivo,
    CompromisoAgenda,
    Delegacion,
    Evidencia,
    ItemMedicion,
    PeriodoMedicion,
    Usuario,
)


def calcular_dias_periodo(periodo: PeriodoMedicion, fecha_corte: date = None):
    """
    Calcula los días computables totales y los días transcurridos hasta la fecha_corte.
    """
    if not fecha_corte:
        fecha_corte = timezone.localdate()

    dias_totales = max((periodo.fecha_termino - periodo.fecha_inicio).days + 1, 1)

    if fecha_corte < periodo.fecha_inicio:
        dias_transcurridos = 0
    elif fecha_corte > periodo.fecha_termino:
        dias_transcurridos = dias_totales
    else:
        dias_transcurridos = (fecha_corte - periodo.fecha_inicio).days + 1

    # Meta esperada al día en % (RN-007): (días transcurridos / días totales) * 100
    meta_esperada_dia_pct = round((dias_transcurridos / dias_totales) * 100.0, 2)
    pct_diario_esperado = round(100.0 / dias_totales, 2)
    umbral_ambar_dia = round(
        meta_esperada_dia_pct * periodo.umbral_semaforo_ambar / 100.0,
        2,
    )
    umbral_verde_dia = round(
        meta_esperada_dia_pct * periodo.umbral_semaforo_verde / 100.0,
        2,
    )

    return {
        'fecha_inicio': periodo.fecha_inicio,
        'fecha_termino': periodo.fecha_termino,
        'dias_totales': dias_totales,
        'dias_transcurridos': dias_transcurridos,
        'pct_diario_esperado': pct_diario_esperado,
        'meta_esperada_pct': meta_esperada_dia_pct,
        'umbral_verde_ratio': periodo.umbral_semaforo_verde,
        'umbral_ambar_ratio': periodo.umbral_semaforo_ambar,
        'umbral_verde_dia': umbral_verde_dia,
        'umbral_60_dia': umbral_ambar_dia,
        'umbral_ambar_dia': umbral_ambar_dia,
        'fecha_corte': fecha_corte
    }


def determinar_color_semaforo(
    cumplimiento_pct: float,
    meta_esperada_pct: float,
    umbral_verde: float = 100.0,
    umbral_ambar: float = 60.0,
):
    """
    Compara el avance con la meta acumulada esperada usando los umbrales del período.
    """
    if meta_esperada_pct <= 0:
        return 'VERDE' if cumplimiento_pct >= 0 else 'ROJO'

    ratio = (cumplimiento_pct / meta_esperada_pct) * 100.0
    if ratio >= umbral_verde:
        return 'VERDE'
    elif ratio >= umbral_ambar:
        return 'AMBAR'
    else:
        return 'ROJO'


def calcular_metricas_funcionario(funcionario: Usuario, periodo: PeriodoMedicion, fecha_corte: date = None):
    """
    Calcula el cumplimiento detallado por ítem de medición, cumplimiento ponderado total
    y estado del semáforo para un funcionario en un período dado.
    """
    info_tiempo = calcular_dias_periodo(periodo, fecha_corte)
    meta_esperada_pct = info_tiempo['meta_esperada_pct']

    items = (
        ItemMedicion.objects.filter(cargo=funcionario.cargo, periodo=periodo, activo=True)
        if periodo.pk
        else ItemMedicion.objects.none()
    )
    detalles_items = []
    total_ponderado_acumulado = 0.0

    # Todas las actividades del funcionario en el rango de fechas del periodo
    actividades_base = Actividad.objects.filter(
        funcionario=funcionario,
        fecha_actividad__gte=periodo.fecha_inicio,
        fecha_actividad__lte=periodo.fecha_termino
    )

    for item in items:
        # Solo actividades cuya evidencia esté APROBADA (RN-009, CA-01, CA-02)
        avance_aprobado = actividades_base.filter(
            item_medicion=item,
            evidencias__estado=Evidencia.Estado.APROBADA
        ).distinct().count()

        # Evidencias en otros estados para seguimiento
        evidencias_pendientes = actividades_base.filter(
            item_medicion=item,
            evidencias__estado=Evidencia.Estado.PENDIENTE
        ).distinct().count()

        evidencias_rechazadas = actividades_base.filter(
            item_medicion=item,
            evidencias__estado=Evidencia.Estado.RECHAZADA
        ).distinct().count()

        # % Cumplimiento del ítem (RN-004)
        if item.meta_cuantitativa > 0:
            cumplimiento_item_pct = (avance_aprobado / item.meta_cuantitativa) * 100.0
        else:
            cumplimiento_item_pct = 0.0

        # Ponderación con tope máximo (RN-005: tope configurable, ej 150%)
        tope = periodo.tope_cumplimiento or 150.0
        cumplimiento_con_tope = min(cumplimiento_item_pct, tope)
        aporte_ponderado = (item.ponderador * cumplimiento_con_tope) / 100.0
        total_ponderado_acumulado += aporte_ponderado

        color_item = determinar_color_semaforo(
            cumplimiento_item_pct,
            meta_esperada_pct,
            periodo.umbral_semaforo_verde,
            periodo.umbral_semaforo_ambar,
        )

        detalles_items.append({
            'item_id': item.id,
            'nombre': item.nombre_item,
            'descripcion': item.descripcion,
            'meta': item.meta_cuantitativa,
            'unidad': item.unidad_medida,
            'ponderador': item.ponderador,
            'avance_aprobado': avance_aprobado,
            'evidencias_pendientes': evidencias_pendientes,
            'evidencias_rechazadas': evidencias_rechazadas,
            'cumplimiento_pct': round(cumplimiento_item_pct, 1),
            'aporte_ponderado': round(aporte_ponderado, 2),
            'semaforo': color_item
        })

    ajustes_aprobados = (
        AjusteIncentivo.objects.filter(
            funcionario=funcionario,
            periodo=periodo,
            estado__in=('APROBADO', 'APLICADO', 'INCENTIVO', 'PENALIZACION'),
        ).select_related('registrado_por', 'aprobadoPor').order_by('fecha_registro', 'pk')
        if periodo.pk
        else AjusteIncentivo.objects.none()
    )
    total_ajustado = total_ponderado_acumulado
    detalles_ajustes = []
    for ajuste in ajustes_aprobados:
        total_anterior = total_ajustado
        total_ajustado = ajuste.aplicar_ajuste(total_anterior)
        detalles_ajustes.append({
            'id': ajuste.pk,
            'tipo': ajuste.get_tipo_display(),
            'motivo': ajuste.motivo,
            'valor_porcentaje': ajuste.valor_porcentaje,
            'responsable': (
                ajuste.registrado_por.get_full_name()
                or ajuste.registrado_por.username
                if ajuste.registrado_por
                else 'Responsable no registrado'
            ),
            'aprobado_por': (
                ajuste.aprobadoPor.get_full_name()
                or ajuste.aprobadoPor.username
                if ajuste.aprobadoPor
                else 'Aprobación no registrada'
            ),
            'estado': ajuste.estado,
            'efecto_pct': round(total_ajustado - total_anterior, 2),
            'resultado_pct': round(total_ajustado, 2),
        })

    # Semáforo global del funcionario
    color_global = determinar_color_semaforo(
        total_ajustado,
        meta_esperada_pct,
        periodo.umbral_semaforo_verde,
        periodo.umbral_semaforo_ambar,
    )

    # Métricas de inactividad y actividad reciente (RF-030)
    ultima_actividad = actividades_base.order_by('-fecha_registro').first()
    fecha_ultimo_ingreso = ultima_actividad.fecha_registro if ultima_actividad else None

    dias_sin_ingreso = None
    if fecha_ultimo_ingreso:
        dias_sin_ingreso = (timezone.now() - fecha_ultimo_ingreso).days

    total_actividades_registradas = actividades_base.count()
    promedio_diario = round(total_actividades_registradas / max(info_tiempo['dias_transcurridos'], 1), 2)

    # Compromisos asignados en el Tubo de Trabajo
    compromisos_total = CompromisoAgenda.objects.filter(responsable=funcionario).count()
    compromisos_realizados = CompromisoAgenda.objects.filter(
        responsable=funcionario, estado=CompromisoAgenda.Estado.REALIZADO
    ).count()
    compromisos_pendientes = compromisos_total - compromisos_realizados

    return {
        'funcionario_id': funcionario.id,
        'nombre_completo': funcionario.get_full_name() or funcionario.username,
        'username': funcionario.username,
        'cargo_id': funcionario.cargo_id,
        'cargo': funcionario.cargo.nombre if funcionario.cargo else "Sin Cargo",
        'delegacion': funcionario.delegacion.nombre if funcionario.delegacion else "Central",
        'delegacion_codigo': funcionario.delegacion.codigo if funcionario.delegacion else "SGR",
        'periodo_nombre': periodo.nombre,
        'meta_esperada_pct': meta_esperada_pct,
        'dias_transcurridos': info_tiempo['dias_transcurridos'],
        'dias_totales': info_tiempo['dias_totales'],
        'total_ponderado_pct': round(total_ponderado_acumulado, 2),
        'total_ajustado_pct': round(total_ajustado, 2),
        'ajustes': detalles_ajustes,
        'semaforo_global': color_global,
        'items': detalles_items,
        'ultima_actividad': fecha_ultimo_ingreso.isoformat() if fecha_ultimo_ingreso else None,
        'dias_sin_ingreso': dias_sin_ingreso,
        'total_actividades': total_actividades_registradas,
        'promedio_diario': promedio_diario,
        'compromisos': {
            'total': compromisos_total,
            'realizados': compromisos_realizados,
            'pendientes': compromisos_pendientes,
            'porcentaje_realizado': round((compromisos_realizados / max(compromisos_total, 1)) * 100, 1)
        }
    }


def calcular_resumen_delegacion(delegacion: Delegacion, periodo: PeriodoMedicion, fecha_corte: date = None):
    """
    Consolida métricas para el Delegado y Coordinador (RF-029).
    """
    info_tiempo = calcular_dias_periodo(periodo, fecha_corte)
    funcionarios = Usuario.objects.filter(
        delegacion=delegacion,
        rol=Usuario.Rol.ADMINISTRATIVO,
        es_delegado=False,
        is_active=True,
    )

    metricas_funcionarios = [
        calcular_metricas_funcionario(f, periodo, fecha_corte) for f in funcionarios
    ]

    total_f = len(metricas_funcionarios)
    if total_f > 0:
        promedio_ponderado = round(sum(m['total_ajustado_pct'] for m in metricas_funcionarios) / total_f, 2)
        conteo_verde = sum(1 for m in metricas_funcionarios if m['semaforo_global'] == 'VERDE')
        conteo_ambar = sum(1 for m in metricas_funcionarios if m['semaforo_global'] == 'AMBAR')
        conteo_rojo = sum(1 for m in metricas_funcionarios if m['semaforo_global'] == 'ROJO')
    else:
        promedio_ponderado = 0.0
        conteo_verde = 0
        conteo_ambar = 0
        conteo_rojo = 0

    semaforo_delegacion = determinar_color_semaforo(
        promedio_ponderado,
        info_tiempo['meta_esperada_pct'],
        periodo.umbral_semaforo_verde,
        periodo.umbral_semaforo_ambar,
    )

    # Compromisos de la delegación en la agenda colectiva
    compromisos_del = CompromisoAgenda.objects.filter(delegacion=delegacion)
    comp_total = compromisos_del.count()
    comp_realizados = compromisos_del.filter(estado=CompromisoAgenda.Estado.REALIZADO).count()
    comp_vencidos = compromisos_del.filter(
        fecha_comprometida__lt=timezone.now().date(),
        estado__in=[CompromisoAgenda.Estado.INGRESADO, CompromisoAgenda.Estado.PENDIENTE, CompromisoAgenda.Estado.EN_PROCESO]
    ).count()

    # Evidencias pendientes de validación ("Check" del Delegado)
    evidencias_pendientes = Evidencia.objects.filter(
        actividad__delegacion=delegacion,
        estado=Evidencia.Estado.PENDIENTE
    ).count()

    # Datos del Delegado y estadísticas de ingreso en terreno
    delegado = Usuario.objects.filter(
        delegacion=delegacion,
        rol=Usuario.Rol.SUPERVISOR,
        es_delegado=True,
        is_active=True,
    ).first()
    nombre_responsable = delegado.get_full_name() if (delegado and delegado.get_full_name()) else (delegado.username if delegado else f"Delegado(a) {delegacion.nombre}")

    corte = fecha_corte or timezone.now().date()
    acts_delegacion = Actividad.objects.filter(
        delegacion=delegacion,
        fecha_actividad__gte=periodo.fecha_inicio,
        fecha_actividad__lte=min(corte, periodo.fecha_termino)
    )
    total_ingresos = acts_delegacion.count()
    ultima_act = acts_delegacion.order_by('-fecha_actividad').first()
    fecha_ultimo_ingreso = ultima_act.fecha_actividad if ultima_act else None
    dias_pasados = (timezone.now().date() - fecha_ultimo_ingreso).days if fecha_ultimo_ingreso else None
    promedio_ingresos_diarios = round(total_ingresos / max(info_tiempo['dias_transcurridos'], 1), 2)

    return {
        'delegacion_id': delegacion.id,
        'nombre': delegacion.nombre,
        'codigo': delegacion.codigo,
        'responsable': nombre_responsable,
        'periodo_nombre': periodo.nombre,
        'meta_esperada_pct': info_tiempo['meta_esperada_pct'],
        'objetivo_al_dia': info_tiempo['meta_esperada_pct'],
        'umbral_60': info_tiempo['umbral_60_dia'],
        'promedio_ponderado_pct': promedio_ponderado,
        'semaforo_delegacion': semaforo_delegacion,
        'total_funcionarios': total_f,
        'total_ingresos': total_ingresos,
        'ultimo_ingreso': fecha_ultimo_ingreso,
        'dias_pasados_ultimo_ingreso': dias_pasados,
        'promedio_ingresos_diarios': promedio_ingresos_diarios,
        'distribucion_semaforo': {
            'verde': conteo_verde,
            'ambar': conteo_ambar,
            'rojo': conteo_rojo
        },
        'compromisos': {
            'total': comp_total,
            'realizados': comp_realizados,
            'pendientes': comp_total - comp_realizados,
            'vencidos': comp_vencidos
        },
        'evidencias_pendientes_check': evidencias_pendientes,
        'funcionarios': metricas_funcionarios
    }


def calcular_resumen_comunal(periodo: PeriodoMedicion, fecha_corte: date = None):
    """
    Consolida métricas para el Coordinador Comunal transversal sobre las 6 delegaciones.
    """
    delegaciones = Delegacion.objects.filter(activo=True)
    resumenes = [calcular_resumen_delegacion(d, periodo, fecha_corte) for d in delegaciones]

    info_tiempo = calcular_dias_periodo(periodo, fecha_corte)
    total_delegaciones = len(resumenes)
    promedio_comunal = round(sum(r['promedio_ponderado_pct'] for r in resumenes) / max(total_delegaciones, 1), 2)
    semaforo_comunal = determinar_color_semaforo(
        promedio_comunal,
        info_tiempo['meta_esperada_pct'],
        periodo.umbral_semaforo_verde,
        periodo.umbral_semaforo_ambar,
    )

    total_evidencias_pendientes = sum(r['evidencias_pendientes_check'] for r in resumenes)
    total_compromisos_vencidos = sum(r['compromisos']['vencidos'] for r in resumenes)
    total_ingresos_comunales = sum(r['total_ingresos'] for r in resumenes)

    return {
        'periodo_nombre': periodo.nombre,
        'info_tiempo': info_tiempo,
        'meta_esperada_pct': info_tiempo['meta_esperada_pct'],
        'promedio_comunal_pct': promedio_comunal,
        'semaforo_comunal': semaforo_comunal,
        'total_evidencias_pendientes': total_evidencias_pendientes,
        'total_compromisos_vencidos': total_compromisos_vencidos,
        'total_ingresos_comunales': total_ingresos_comunales,
        'delegaciones': resumenes
    }
