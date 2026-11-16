"""Informe mensual de disponibilidad frente a la meta (RNF-001)."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sgr_app.models import Indisponibilidad
from sgr_app.services.disponibilidad import calcular_disponibilidad


class Command(BaseCommand):
    help = 'Calcula la disponibilidad de un mes dentro de la jornada operativa.'

    def add_arguments(self, parser):
        parser.add_argument('--mes', help='Mes AAAA-MM (por defecto, el mes actual).')
        parser.add_argument('--meta', type=float, help='Meta en %% (por defecto DISPONIBILIDAD_META_PCT).')

    def handle(self, *args, **options):
        ahora = timezone.localtime()
        if options['mes']:
            try:
                anio, mes = (int(x) for x in options['mes'].split('-'))
            except ValueError as exc:
                raise CommandError('Usa el formato --mes AAAA-MM.') from exc
        else:
            anio, mes = ahora.year, ahora.month

        incidentes = list(Indisponibilidad.objects.all())
        # Incluye una caída en curso que aún no tiene fin registrado.
        try:
            estado = json.loads(Path(settings.MONITOR_STATE_FILE).read_text(encoding='utf-8'))
            if estado.get('caida_desde'):
                incidentes.append(SimpleNamespace(
                    inicio=datetime.fromisoformat(estado['caida_desde']), fin=timezone.now(),
                    tipo='CAIDA', causa=f'(en curso) {estado.get("causa", "")}',
                ))
        except (OSError, ValueError):
            pass

        en_curso = (anio, mes) == (ahora.year, ahora.month)
        informe = calcular_disponibilidad(
            anio, mes, incidentes, meta_pct=options['meta'], hasta=timezone.now() if en_curso else None,
        )

        self.stdout.write(f'Disponibilidad {anio}-{mes:02d} (jornada {settings.JORNADA_HORAS or "24 h"}, '
                          f'días {settings.JORNADA_DIAS})')
        self.stdout.write(f'  Tiempo de jornada:      {informe.minutos_jornada:,.0f} min')
        self.stdout.write(f'  Tiempo no disponible:   {informe.minutos_no_disponible:,.1f} min')
        self.stdout.write(f'  Disponibilidad:         {informe.disponibilidad_pct:.3f} %')
        resultado = 'CUMPLE' if informe.cumple_meta else 'NO CUMPLE'
        self.stdout.write(f'  Meta {informe.meta_pct} %:            {resultado}')
        relevantes = [
            i for i in incidentes
            if timezone.localtime(i.inicio).year == anio and timezone.localtime(i.inicio).month == mes
        ]
        if relevantes:
            self.stdout.write('  Incidentes registrados:')
            for incidente in sorted(relevantes, key=lambda i: i.inicio):
                inicio = timezone.localtime(incidente.inicio)
                minutos = (incidente.fin - incidente.inicio).total_seconds() / 60
                self.stdout.write(
                    f'    {inicio:%Y-%m-%d %H:%M}  {minutos:7.1f} min  {incidente.tipo:<14} {incidente.causa}'
                )
