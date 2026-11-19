"""
Alinea los roles con los actores de la especificación de casos de uso:
Administrador, Coordinador, Delegado, Funcionario y Usuario de consulta.

- SUPERVISOR con perfil delegado -> DELEGADO
- SUPERVISOR                     -> COORDINADOR
- ADMINISTRATIVO                 -> FUNCIONARIO

El campo es_delegado desaparece: Delegado pasa a ser un rol propio.
Los roles adicionales (tabla Roles) con los nombres antiguos se renombran.
No se elimina ningún registro y el historial de auditoría no se altera.
"""

from django.db import migrations, models


RENOMBRES_ROL_ADICIONAL = (
    ('SUPERVISOR', 'COORDINADOR'),
    ('ADMINISTRATIVO', 'FUNCIONARIO'),
)


def a_actores(apps, schema_editor):
    Usuario = apps.get_model('sgr_app', 'Usuario')
    Rol = apps.get_model('sgr_app', 'Rol')

    Usuario.objects.filter(rol='SUPERVISOR', es_delegado=True).update(rol='DELEGADO')
    Usuario.objects.filter(rol='SUPERVISOR').update(rol='COORDINADOR')
    Usuario.objects.filter(rol='ADMINISTRATIVO').update(rol='FUNCIONARIO')

    for antiguo, nuevo in RENOMBRES_ROL_ADICIONAL:
        if Rol.objects.filter(nombre_rol__iexact=nuevo).exists():
            continue
        Rol.objects.filter(nombre_rol__iexact=antiguo).update(nombre_rol=nuevo)


def a_roles_anteriores(apps, schema_editor):
    Usuario = apps.get_model('sgr_app', 'Usuario')
    Rol = apps.get_model('sgr_app', 'Rol')

    Usuario.objects.filter(rol='DELEGADO').update(rol='SUPERVISOR', es_delegado=True)
    Usuario.objects.filter(rol='COORDINADOR').update(rol='SUPERVISOR')
    Usuario.objects.filter(rol='FUNCIONARIO').update(rol='ADMINISTRATIVO')

    for antiguo, nuevo in RENOMBRES_ROL_ADICIONAL:
        if Rol.objects.filter(nombre_rol__iexact=antiguo).exists():
            continue
        Rol.objects.filter(nombre_rol__iexact=nuevo).update(nombre_rol=antiguo)


class Migration(migrations.Migration):

    dependencies = [
        ('sgr_app', '0039_alertas_generadas'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='usuario',
            name='usuario_rol_valido',
        ),
        migrations.RemoveConstraint(
            model_name='usuario',
            name='delegado_debe_ser_supervisor',
        ),
        migrations.AlterField(
            model_name='usuario',
            name='rol',
            field=models.CharField(
                choices=[
                    ('ADMINISTRADOR', 'Administrador'),
                    ('COORDINADOR', 'Coordinador'),
                    ('DELEGADO', 'Delegado'),
                    ('FUNCIONARIO', 'Funcionario'),
                    ('CONSULTA', 'Usuario de consulta'),
                ],
                default='FUNCIONARIO',
                max_length=20,
            ),
        ),
        migrations.RunPython(a_actores, a_roles_anteriores),
        migrations.RemoveField(
            model_name='usuario',
            name='es_delegado',
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=models.CheckConstraint(
                condition=models.Q(rol__in=[
                    'ADMINISTRADOR',
                    'COORDINADOR',
                    'DELEGADO',
                    'FUNCIONARIO',
                    'CONSULTA',
                ]),
                name='usuario_rol_valido',
            ),
        ),
    ]
