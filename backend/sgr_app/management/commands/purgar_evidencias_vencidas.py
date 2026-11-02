from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from sgr_app.models import Evidencia


class Command(BaseCommand):
    help = 'Elimina archivos de evidencia vencidos y conserva su registro y metadatos.'

    def handle(self, *args, **options):
        fecha_limite = timezone.now() - timedelta(
            days=settings.EVIDENCE_RETENTION_DAYS
        )
        evidencias = Evidencia.objects.filter(
            fecha_subida__lt=fecha_limite,
            archivos_eliminados_en__isnull=True,
        ).filter(
            Q(archivo__isnull=False)
            | Q(archivo_foto__isnull=False)
            | Q(archivo_foto_2__isnull=False)
            | Q(archivo_foto_3__isnull=False)
        )

        eliminadas = 0
        for evidencia in evidencias.iterator():
            campos_actualizados = ['archivos_eliminados_en']
            for campo in ('archivo', 'archivo_foto', 'archivo_foto_2', 'archivo_foto_3'):
                archivo = getattr(evidencia, campo)
                if archivo and archivo.name:
                    archivo.storage.delete(archivo.name)
                    setattr(evidencia, campo, None)
                    campos_actualizados.append(campo)

            evidencia.archivos_eliminados_en = timezone.now()
            evidencia.save(update_fields=campos_actualizados)
            eliminadas += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Archivos de {eliminadas} evidencia(s) vencida(s) eliminados; '
                'los registros y metadatos se conservaron.'
            )
        )
