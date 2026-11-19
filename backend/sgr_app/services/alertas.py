"""Reglas de alerta vigentes (CU-07) y destinatarios por rol (HU-31)."""
from dataclasses import dataclass, field

from django.utils import timezone

TODOS_LOS_ROLES = ['FUNCIONARIO', 'DELEGADO', 'COORDINADOR', 'ADMINISTRADOR']

# Valores usados mientras el Administrador no registre una versión propia.
# El significado del umbral depende del tipo de alerta.
REGLAS_POR_DEFECTO = {
    'VENCIMIENTO': {'umbral': 0, 'unidad': 'días de gracia tras la fecha comprometida'},
    'PLAZO_PROXIMO': {'umbral': 7, 'unidad': 'días de anticipación'},
    'EVIDENCIA_PENDIENTE': {'umbral': 0, 'unidad': 'días mínimos de espera de validación'},
    'RENDIMIENTO_BAJO': {
        'umbral': None,
        'unidad': '% del avance esperado (vacío = umbral ámbar del período)',
    },
    'INACTIVIDAD': {'umbral': 7, 'unidad': 'días sin registros'},
}


@dataclass
class ReglaVigente:
    tipo: str
    activa: bool = True
    umbral: float | None = None
    destinatarios: list = field(default_factory=lambda: list(TODOS_LOS_ROLES))
    vigente_desde: object = None
    es_por_defecto: bool = True
    unidad: str = ''
    version_id: int | None = None


def reglas_vigentes(momento=None):
    from sgr_app.models import ReglaAlerta

    momento = momento or timezone.now()
    reglas = {}
    for tipo, base in REGLAS_POR_DEFECTO.items():
        version = (
            ReglaAlerta.objects.filter(tipo=tipo, vigente_desde__lte=momento)
            .order_by('-vigente_desde', '-pk')
            .first()
        )
        if version:
            reglas[tipo] = ReglaVigente(
                tipo=tipo,
                activa=version.activa,
                umbral=version.umbral,
                destinatarios=list(version.destinatarios or []),
                vigente_desde=version.vigente_desde,
                es_por_defecto=False,
                unidad=base['unidad'],
                version_id=version.pk,
            )
        else:
            reglas[tipo] = ReglaVigente(tipo=tipo, umbral=base['umbral'], unidad=base['unidad'])
    return reglas


def perfil_destinatario(user):
    if user.es_admin():
        return 'ADMINISTRADOR'
    if user.esta_limitado_a_delegacion:
        return 'DELEGADO'
    if user.es_supervisor:
        return 'COORDINADOR'
    return 'FUNCIONARIO'


def aplica_a(regla, user):
    return regla.activa and perfil_destinatario(user) in regla.destinatarios


def reglas_de_alertas_generadas(claves):
    """CU-07: regla con que se generó cada alerta ya existente, por clave."""
    from sgr_app.models import AlertaGenerada

    claves = list(claves)
    if not claves:
        return {}
    return {
        generada.clave: ReglaVigente(
            tipo=generada.tipo,
            activa=generada.regla_activa,
            umbral=generada.regla_umbral,
            destinatarios=list(generada.regla_destinatarios or []),
            vigente_desde=generada.regla.vigente_desde if generada.regla_id else None,
            es_por_defecto=generada.regla_id is None,
            unidad=REGLAS_POR_DEFECTO.get(generada.tipo, {}).get('unidad', ''),
            version_id=generada.regla_id,
        )
        for generada in AlertaGenerada.objects.filter(clave__in=claves).select_related('regla')
    }


def registrar_alertas_generadas(alertas_con_regla):
    """Guarda las alertas nuevas con la regla que las generó (las existentes no cambian)."""
    from sgr_app.models import AlertaGenerada

    nuevas = [
        AlertaGenerada(
            clave=clave,
            tipo=regla.tipo,
            regla_id=regla.version_id,
            regla_activa=regla.activa,
            regla_umbral=regla.umbral,
            regla_destinatarios=list(regla.destinatarios),
        )
        for clave, regla in alertas_con_regla
    ]
    if nuevas:
        AlertaGenerada.objects.bulk_create(nuevas, ignore_conflicts=True)
