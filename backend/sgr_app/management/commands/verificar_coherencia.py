"""Verifica la coherencia de los datos sin modificarlos.

Uso:
    python manage.py verificar_coherencia              # informe legible
    python manage.py verificar_coherencia --json       # para otras herramientas
    python manage.py verificar_coherencia --alertar    # además envía correo si hay problemas
    python manage.py verificar_coherencia --estricto   # código de salida 1 también con advertencias

Código de salida: 0 sin problemas críticos, 1 si hay críticos (o advertencias con --estricto).
"""

import json
import sys

from django.core.management.base import BaseCommand
from django.utils import timezone

from sgr_app.services.coherencia import ADVERTENCIA, CRITICO, INFO, total_chequeos, verificar_coherencia
from sgr_app.services.operacion import enviar_alerta, registrar_tarea

ICONOS = {CRITICO: '[CRITICO]', ADVERTENCIA: '[ADVERTENCIA]', INFO: '[INFO]'}


class Command(BaseCommand):
    help = 'Revisa la coherencia de los datos (solo lectura) e informa los hallazgos por severidad.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Salida en formato JSON.')
        parser.add_argument('--alertar', action='store_true', help='Envía un correo si hay críticos o advertencias.')
        parser.add_argument('--estricto', action='store_true', help='Falla también ante advertencias.')

    def handle(self, *args, **options):
        with registrar_tarea('verificar_coherencia') as resultado:
            hallazgos = verificar_coherencia()
            conteo = {s: sum(1 for h in hallazgos if h.severidad == s) for s in (CRITICO, ADVERTENCIA, INFO)}
            resultado['detalle'] = (
                f'{conteo[CRITICO]} crítico(s), {conteo[ADVERTENCIA]} advertencia(s), {conteo[INFO]} info'
            )

        if options['json']:
            self.stdout.write(json.dumps({
                'generado': timezone.now().isoformat(),
                'resumen': conteo,
                'hallazgos': [h.como_dict() for h in hallazgos],
            }, ensure_ascii=False, indent=2, default=str))
        else:
            self._imprimir(hallazgos, conteo)

        if options['alertar'] and (conteo[CRITICO] or conteo[ADVERTENCIA]):
            lineas = [
                f'{h.severidad}: {h.descripcion} ({h.cantidad})'
                for h in hallazgos if h.severidad in (CRITICO, ADVERTENCIA)
            ]
            enviar_alerta(
                f'Coherencia de datos: {conteo[CRITICO]} crítico(s), {conteo[ADVERTENCIA]} advertencia(s)',
                '\n'.join(lineas) + '\n\nDetalle: python manage.py verificar_coherencia',
            )

        if conteo[CRITICO] or (options['estricto'] and conteo[ADVERTENCIA]):
            sys.exit(1)

    def _imprimir(self, hallazgos, conteo):
        self.stdout.write(f'Verificación de coherencia de datos - {timezone.localtime():%d/%m/%Y %H:%M}')
        self.stdout.write(f'Chequeos de negocio: {total_chequeos()} + integridad referencial de todas las tablas\n')
        if not hallazgos:
            self.stdout.write(self.style.SUCCESS('Sin hallazgos: los datos son coherentes.'))
            return
        estilos = {CRITICO: self.style.ERROR, ADVERTENCIA: self.style.WARNING, INFO: lambda texto: texto}
        for hallazgo in hallazgos:
            self.stdout.write(estilos[hallazgo.severidad](
                f'{ICONOS[hallazgo.severidad]} {hallazgo.descripcion}: {hallazgo.cantidad}'
            ))
            self.stdout.write(f'    código: {hallazgo.codigo}')
            if hallazgo.ejemplos:
                self.stdout.write(f'    ejemplos (id): {", ".join(str(e) for e in hallazgo.ejemplos)}')
            if hallazgo.recomendacion:
                self.stdout.write(f'    acción: {hallazgo.recomendacion}')
        self.stdout.write(
            f'\nResumen: {conteo[CRITICO]} crítico(s), {conteo[ADVERTENCIA]} advertencia(s), {conteo[INFO]} informativo(s).'
        )
