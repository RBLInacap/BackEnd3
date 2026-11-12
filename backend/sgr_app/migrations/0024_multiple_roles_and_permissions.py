from django.db import migrations, models


def migrar_roles_asignados(apps, schema_editor):
    Usuario = apps.get_model('sgr_app', 'Usuario')
    through = Usuario.roles.through
    alias = schema_editor.connection.alias
    asignaciones = (
        Usuario.objects.using(alias)
        .exclude(rol_entidad_id__isnull=True)
        .values_list('pk', 'rol_entidad_id')
    )
    through.objects.using(alias).bulk_create(
        [
            through(usuario_id=usuario_id, rol_id=rol_id)
            for usuario_id, rol_id in asignaciones
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('sgr_app', '0023_passwordresetcode'),
    ]

    operations = [
        migrations.AddField(
            model_name='rol',
            name='permisos',
            field=models.JSONField(blank=True, default=list, verbose_name='Permisos'),
        ),
        migrations.AddField(
            model_name='usuario',
            name='roles',
            field=models.ManyToManyField(
                blank=True,
                related_name='usuarios',
                to='sgr_app.rol',
                verbose_name='Roles adicionales',
            ),
        ),
        migrations.RunPython(migrar_roles_asignados, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='usuario',
            name='rol_entidad',
        ),
    ]
