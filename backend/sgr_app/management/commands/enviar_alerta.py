"""Envía una alerta por correo; lo usa systemd cuando falla una tarea (OnFailure)."""

from django.core.management.base import BaseCommand

from sgr_app.services.operacion import enviar_alerta


class Command(BaseCommand):
    help = 'Envía una alerta por correo a ALERTAS_EMAIL.'

    def add_arguments(self, parser):
        parser.add_argument('--asunto', required=True)
        parser.add_argument('--mensaje', default='Revisa: sudo journalctl -u sgr-* --since today')

    def handle(self, *args, **options):
        enviar_alerta(options['asunto'], options['mensaje'])
