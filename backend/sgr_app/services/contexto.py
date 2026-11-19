"""Usuario de la petición en curso, para eventos registrados desde los modelos."""
from contextvars import ContextVar

_usuario_actual = ContextVar('sgr_usuario_actual', default=None)


def fijar_usuario_actual(usuario):
    return _usuario_actual.set(usuario)


def limpiar_usuario_actual(token):
    _usuario_actual.reset(token)


def usuario_actual():
    usuario = _usuario_actual.get()
    if usuario is not None and getattr(usuario, 'is_authenticated', False):
        return usuario
    return None
