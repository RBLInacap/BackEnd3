from rest_framework.permissions import SAFE_METHODS, BasePermission


READ_ONLY_MAINTENANCE_VIEWSETS = frozenset({
    'DelegacionViewSet',
    'CargoViewSet',
    'UsuarioViewSet',
    'PeriodoMedicionViewSet',
    'CatalogoServicioViewSet',
    'ItemMedicionViewSet',
})


class AuthenticatedRolePermission(BasePermission):
    """Require login, keep maintenance APIs read-only, and authorize other mutations."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True

        if (
            view.__class__.__name__ in READ_ONLY_MAINTENANCE_VIEWSETS
            and getattr(view, 'action', None) in {
                'create',
                'update',
                'partial_update',
                'destroy',
            }
        ):
            return False

        role = getattr(user, 'rol', None)
        if user.is_superuser or role == 'ADMINISTRADOR' or user.es_supervisor:
            return True
        action = getattr(view, 'action', None)
        if user.es_delegado:
            if view.__class__.__name__ == 'EvidenciaViewSet':
                return action == 'validar'
            if view.__class__.__name__ == 'ActividadViewSet':
                return action == 'create'
            if view.__class__.__name__ == 'CompromisoAgendaViewSet':
                return action in {'create', 'update', 'partial_update', 'destroy', 'cambiar_estado'}
        return (
            role == 'ADMINISTRATIVO'
            and view.__class__.__name__ == 'ActividadViewSet'
            and getattr(view, 'action', None) == 'create'
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        role = getattr(user, 'rol', None)
        if user.is_superuser or role == 'ADMINISTRADOR' or user.es_supervisor:
            return True
        if user.es_delegado:
            delegation_id = user.delegacion_id
            if not delegation_id:
                return False
            if hasattr(obj, 'delegacion_id'):
                return obj.delegacion_id == delegation_id
            if hasattr(obj, 'actividad'):
                return obj.actividad.delegacion_id == delegation_id
            return False
        if role == 'ADMINISTRATIVO':
            if hasattr(obj, 'funcionario_id'):
                return obj.funcionario_id == user.pk
            if hasattr(obj, 'actividad'):
                return obj.actividad.funcionario_id == user.pk
        return False


class AuditAccessPermission(BasePermission):
    """Restrict audit-log access to municipal administrators and coordinators."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or getattr(user, 'rol', None) == 'ADMINISTRADOR'
                or user.es_supervisor
            )
        )
