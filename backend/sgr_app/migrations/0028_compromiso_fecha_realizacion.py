from django.db import migrations, models


def completar_fechas_realizacion(apps, schema_editor):
    Compromiso = apps.get_model('sgr_app', 'CompromisoAgenda')
    db_alias = schema_editor.connection.alias
    Compromiso.objects.using(db_alias).filter(
        estado__in=('COMPLETADO', 'REALIZADO', 'CUMPLIDO'),
    ).update(fecha_realizacion=models.F('actualizado_en'))


class Migration(migrations.Migration):

    dependencies = [
        ('sgr_app', '0027_actividad_codigo_y_anulacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='compromisoagenda',
            name='fecha_realizacion',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            completar_fechas_realizacion,
            migrations.RunPython.noop,
        ),
    ]
