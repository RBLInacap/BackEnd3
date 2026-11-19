from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from sgr_app.models import Evidencia
from sgr_app.services.operacion import registrar_tarea


class Command(BaseCommand):
    """Retención de evidencias (RNF-017) sin borrado físico.

    Por normativa nada se elimina: al vencer el plazo de retención, los archivos
    se deshabilitan (dejan de mostrarse y de entregarse por la aplicación y la
    API), pero se conservan en el almacenamiento junto con su registro.
    El nombre del comando se mantiene por compatibilidad con los temporizadores.
    """

    help = 'Deshabilita (sin borrar) los archivos de evidencia con la retención vencida.'

    def handle(self, *args, **options):
        # RNF-018: se registra cada ejecución y se alerta si falla.
        with registrar_tarea('purgar_evidencias_vencidas') as resultado:
            deshabilitadas = self._deshabilitar()
            resultado['detalle'] = f'{deshabilitadas} evidencia(s) con archivos deshabilitados'
        self.stdout.write(
            self.style.SUCCESS(
                f'Archivos de {deshabilitadas} evidencia(s) vencida(s) deshabilitados; '
                'los archivos, registros y metadatos se conservaron.'
            )
        )

    def _deshabilitar(self):
        fecha_limite = timezone.now() - timedelta(
            days=settings.EVIDENCE_RETENTION_DAYS
        )
        evidencias = Evidencia.objects.filter(
            fecha_subida__lt=fecha_limite,
            archivos_deshabilitados_en__isnull=True,
        ).filter(
            Q(archivo__isnull=False)
            | Q(archivo_foto__isnull=False)
            | Q(archivo_foto_2__isnull=False)
            | Q(archivo_foto_3__isnull=False)
        )
        return evidencias.update(archivos_deshabilitados_en=timezone.now())
