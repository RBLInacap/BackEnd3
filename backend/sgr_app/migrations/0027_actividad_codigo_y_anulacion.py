import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def asignar_codigos_existentes(apps, schema_editor):
    Actividad = apps.get_model('sgr_app', 'Actividad')
    db_alias = schema_editor.connection.alias
    for actividad in Actividad.objects.using(db_alias).only('pk').iterator():
        Actividad.objects.using(db_alias).filter(pk=actividad.pk).update(
            codigo_registro=f'ACT-{actividad.pk:08d}',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('sgr_app', '0026_versionconfiguracionperiodo'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='actividad',
            name='anulada',
            field=models.BooleanField(default=False, verbose_name='Actividad Anulada'),
        ),
        migrations.AddField(
            model_name='actividad',
            name='anulada_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='actividad',
            name='anulada_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='actividades_anuladas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='actividad',
            name='codigo_registro',
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                null=True,
                verbose_name='Código de Actividad',
            ),
        ),
        migrations.AddField(
            model_name='actividad',
            name='motivo_anulacion',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RunPython(asignar_codigos_existentes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='actividad',
            name='codigo_registro',
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                unique=True,
                verbose_name='Código de Actividad',
            ),
        ),
    ]
