"""Protección de la tabla de auditoría a nivel de base de datos (RNF-008).

Los triggers impiden UPDATE y DELETE sobre `Auditorias` incluso fuera de Django
(phpMyAdmin, consola SQL, scripts). Solo quien tenga privilegios para eliminar
los triggers podría alterar el historial, y esa acción queda fuera del uso
normal de la aplicación.
"""

MENSAJE = 'Los registros de auditoria son inmutables'

TRIGGERS = ('auditorias_no_update', 'auditorias_no_delete')


def crear_triggers_auditoria(connection):
    vendor = connection.vendor
    with connection.cursor() as cursor:
        if vendor == 'sqlite':
            for nombre, operacion in zip(TRIGGERS, ('UPDATE', 'DELETE')):
                cursor.execute(f'DROP TRIGGER IF EXISTS {nombre}')
                cursor.execute(
                    f'CREATE TRIGGER {nombre} BEFORE {operacion} ON Auditorias '
                    f"BEGIN SELECT RAISE(ABORT, '{MENSAJE}'); END"
                )
        elif vendor == 'mysql':
            for nombre, operacion in zip(TRIGGERS, ('UPDATE', 'DELETE')):
                cursor.execute(f'DROP TRIGGER IF EXISTS {nombre}')
                cursor.execute(
                    f'CREATE TRIGGER {nombre} BEFORE {operacion} ON Auditorias '
                    f"FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{MENSAJE}'"
                )


def eliminar_triggers_auditoria(connection):
    if connection.vendor not in ('sqlite', 'mysql'):
        return
    with connection.cursor() as cursor:
        for nombre in TRIGGERS:
            cursor.execute(f'DROP TRIGGER IF EXISTS {nombre}')
