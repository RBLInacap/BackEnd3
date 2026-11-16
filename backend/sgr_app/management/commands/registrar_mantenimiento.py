"""Registra una ventana de mantenimiento planificado (excluida del cálculo de disponibilidad)."""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sgr_app.models import Indisponibilidad


class Command(BaseCommand):
    help = 'Registra un mantenimiento planificado: --inicio "AAAA-MM-DD HH:MM" --fin ... --motivo ...'

    def add_arguments(self, parser):
        parser.add_argument('--inicio', required=True)
        parser.add_argument('--fin', required=True)
        parser.add_argument('--motivo', required=True)

    def handle(self, *args, **options):
        try:
            inicio = timezone.make_aware(datetime.strptime(options['inicio'], '%Y-%m-%d %H:%M'))
            fin = timezone.make_aware(datetime.strptime(options['fin'], '%Y-%m-%d %H:%M'))
        except ValueError as exc:
            raise CommandError('Usa el formato "AAAA-MM-DD HH:MM" (hora local).') from exc
        if fin <= inicio:
            raise CommandError('El fin debe ser posterior al inicio.')
        Indisponibilidad.objects.create(
            inicio=inicio, fin=fin, tipo=Indisponibilidad.Tipo.MANTENIMIENTO, causa=options['motivo'],
        )
        self.stdout.write(self.style.SUCCESS('Mantenimiento registrado.'))
