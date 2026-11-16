"""Cálculo de disponibilidad mensual dentro de la jornada operativa (RNF-001).

Disponibilidad = 1 - (tiempo no disponible / tiempo de jornada), excluyendo de
ambos los mantenimientos planificados. Los periodos SIN_MONITOREO (servidor
apagado o monitor detenido) cuentan como no disponibles: no hay evidencia de
que el sistema respondiera.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone


def _parse_jornada():
    dias = {int(d) for d in str(settings.JORNADA_DIAS).split(',') if str(d).strip() != ''}
    horas = (settings.JORNADA_HORAS or '').strip()
    if horas:
        desde, hasta = (time.fromisoformat(h.strip()) for h in horas.split('-'))
    else:
        desde, hasta = time(0, 0), None   # día completo
    return dias, desde, hasta


def ventanas_jornada(anio, mes):
    """Intervalos (inicio, fin) con zona horaria local de la jornada del mes."""
    tz = timezone.get_current_timezone()
    dias, desde, hasta = _parse_jornada()
    ventanas = []
    for dia in range(1, calendar.monthrange(anio, mes)[1] + 1):
        fecha = date(anio, mes, dia)
        if fecha.weekday() not in dias:
            continue
        inicio = timezone.make_aware(datetime.combine(fecha, desde), tz)
        if hasta is None:
            fin = timezone.make_aware(datetime.combine(fecha + timedelta(days=1), time(0, 0)), tz)
        else:
            fin = timezone.make_aware(datetime.combine(fecha, hasta), tz)
        ventanas.append((inicio, fin))
    return ventanas


def _unir(intervalos):
    unidos = []
    for inicio, fin in sorted(i for i in intervalos if i[1] > i[0]):
        if unidos and inicio <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], fin))
        else:
            unidos.append((inicio, fin))
    return unidos


def _restar(intervalos, a_restar):
    resultado = []
    for inicio, fin in intervalos:
        partes = [(inicio, fin)]
        for r_ini, r_fin in a_restar:
            nuevas = []
            for p_ini, p_fin in partes:
                if r_fin <= p_ini or r_ini >= p_fin:
                    nuevas.append((p_ini, p_fin))
                    continue
                if r_ini > p_ini:
                    nuevas.append((p_ini, r_ini))
                if r_fin < p_fin:
                    nuevas.append((r_fin, p_fin))
            partes = nuevas
        resultado.extend(partes)
    return resultado


def _duracion_solapada(intervalos, ventanas):
    total = timedelta()
    for inicio, fin in intervalos:
        for v_ini, v_fin in ventanas:
            solape = min(fin, v_fin) - max(inicio, v_ini)
            if solape > timedelta():
                total += solape
    return total


@dataclass
class InformeDisponibilidad:
    anio: int
    mes: int
    meta_pct: float
    minutos_jornada: float
    minutos_no_disponible: float
    disponibilidad_pct: float
    incidentes: list = field(default_factory=list)

    @property
    def cumple_meta(self):
        return self.disponibilidad_pct >= self.meta_pct


def calcular_disponibilidad(anio, mes, incidentes, meta_pct=None, hasta=None):
    """
    incidentes: iterable de objetos con inicio, fin y tipo.
    hasta: limita el cálculo al momento actual si el mes está en curso.
    """
    meta_pct = settings.DISPONIBILIDAD_META_PCT if meta_pct is None else meta_pct
    ventanas = ventanas_jornada(anio, mes)
    if hasta is not None:
        ventanas = [(i, min(f, hasta)) for i, f in ventanas if i < hasta]

    incidentes = list(incidentes)
    mantenimiento = _unir((i.inicio, i.fin) for i in incidentes if i.tipo == 'MANTENIMIENTO')
    caidas = _unir((i.inicio, i.fin) for i in incidentes if i.tipo != 'MANTENIMIENTO')
    caidas = _restar(caidas, mantenimiento)
    ventanas_efectivas = _restar(ventanas, mantenimiento)

    jornada = sum(((f - i) for i, f in ventanas_efectivas), timedelta())
    no_disponible = _duracion_solapada(caidas, ventanas_efectivas)
    minutos_jornada = jornada.total_seconds() / 60
    minutos_caida = no_disponible.total_seconds() / 60
    disponibilidad = 100.0 if minutos_jornada == 0 else (1 - minutos_caida / minutos_jornada) * 100
    return InformeDisponibilidad(
        anio=anio, mes=mes, meta_pct=meta_pct,
        minutos_jornada=round(minutos_jornada, 1),
        minutos_no_disponible=round(minutos_caida, 1),
        disponibilidad_pct=round(disponibilidad, 3),
        incidentes=incidentes,
    )
