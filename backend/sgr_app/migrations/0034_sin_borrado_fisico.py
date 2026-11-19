"""Normativa: nada se elimina.

Evidencia: `archivos_eliminados_en` pasa a `archivos_deshabilitados_en` (renombre,
sin pérdida de datos). Los archivos vencidos se deshabilitan, no se borran.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sgr_app', '0033_compromiso_anulacion'),
    ]

    operations = [
        migrations.RenameField(
            model_name='evidencia',
            old_name='archivos_eliminados_en',
            new_name='archivos_deshabilitados_en',
        ),
    ]
