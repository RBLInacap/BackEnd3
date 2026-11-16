from django.conf import settings
from django.db import migrations

from sgr_app.db_auditoria import crear_triggers_auditoria, eliminar_triggers_auditoria


def crear(apps, schema_editor):
    # En la base temporal de pruebas no se crean: TransactionTestCase vacía las
    # tablas con DELETE. Los triggers se prueban explícitamente en test_fase_a.
    if getattr(settings, 'TESTING', False):
        return
    crear_triggers_auditoria(schema_editor.connection)


def eliminar(apps, schema_editor):
    eliminar_triggers_auditoria(schema_editor.connection)


class Migration(migrations.Migration):

    dependencies = [
        ('sgr_app', '0030_bloqueo_acceso'),
    ]

    operations = [
        migrations.RunPython(crear, eliminar),
    ]
