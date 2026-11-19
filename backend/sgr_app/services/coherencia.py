"""Verificación de coherencia de los datos (solo lectura).

Detecta registros que la base acepta pero que contradicen el modelo de
negocio: claves huérfanas, columnas duplicadas que divergen, reglas que solo
se validan en la aplicación e identidades ambiguas. No modifica nada.

Severidades:
- CRITICO: puede producir resultados incorrectos o fallas (corregir pronto).
- ADVERTENCIA: dato inconsistente que distorsiona indicadores o trazabilidad.
- INFO: deuda heredada, sin efecto inmediato.
"""

from dataclasses import dataclass, field

from django.apps import apps
from django.db import models
from django.db.models import Count, Exists, F, OuterRef, Q, Sum
from django.db.models.functions import Lower

CRITICO = 'CRITICO'
ADVERTENCIA = 'ADVERTENCIA'
INFO = 'INFO'
ORDEN_SEVERIDAD = {CRITICO: 0, ADVERTENCIA: 1, INFO: 2}
MAX_EJEMPLOS = 5


@dataclass
class Hallazgo:
    codigo: str
    severidad: str
    descripcion: str
    cantidad: int
    ejemplos: list = field(default_factory=list)
    recomendacion: str = ''

    def como_dict(self):
        return {
            'codigo': self.codigo, 'severidad': self.severidad, 'descripcion': self.descripcion,
            'cantidad': self.cantidad, 'ejemplos': self.ejemplos, 'recomendacion': self.recomendacion,
        }


def _hallazgo(codigo, severidad, descripcion, queryset, recomendacion='', campo_ejemplo='pk'):
    cantidad = queryset.count()
    ejemplos = list(queryset.order_by(campo_ejemplo).values_list(campo_ejemplo, flat=True)[:MAX_EJEMPLOS]) if cantidad else []
    return Hallazgo(codigo, severidad, descripcion, cantidad, ejemplos, recomendacion)


# --- 1. Integridad referencial ---------------------------------------------------------

def chequear_claves_huerfanas():
    """Claves foráneas que apuntan a registros inexistentes (todas las tablas del sistema)."""
    resultados = []
    for modelo in apps.get_app_config('sgr_app').get_models():
        for campo in modelo._meta.concrete_fields:
            if not isinstance(campo, models.ForeignKey):
                continue
            destino = campo.related_model
            huerfanos = modelo._default_manager.exclude(**{f'{campo.attname}__isnull': True}).exclude(
                **{f'{campo.attname}__in': destino._default_manager.values('pk')}
            )
            hallazgo = _hallazgo(
                f'fk_huerfana.{modelo.__name__}.{campo.name}', CRITICO,
                f'{modelo.__name__}.{campo.name} apunta a un {destino.__name__} que no existe',
                huerfanos, 'Restaurar el registro referenciado o corregir la referencia.',
            )
            if hallazgo.cantidad:
                resultados.append(hallazgo)
    return resultados


# --- 2. Chequeos de negocio -----------------------------------------------------------------

def _chequeos_negocio():
    from sgr_app.models import (
        Actividad, AjusteIncentivo, CompromisoAgenda, Evidencia, Indicador, Indisponibilidad,
        ItemMedicion, MetaModel, ObservacionCompromiso, PeriodoMedicion, RegistroAuditoria, Usuario,
    )

    realizados = ('COMPLETADO', 'REALIZADO', 'CUMPLIDO')
    estados_compromiso = [valor for valor, _ in CompromisoAgenda.ESTADO_CHOICES]
    estados_evidencia = list(Evidencia.Estado.values)
    tipos_positivos, tipos_negativos = ('INCENTIVO', 'BONIFICACION'), ('PENALIZACION', 'DESCUENTO')
    estados_ajuste = ('PENDIENTE', 'APROBADO', 'RECHAZADO')
    estados_ajuste_heredados = ('APLICADO', 'INCENTIVO', 'PENALIZACION')

    periodo_superpuesto = PeriodoMedicion.objects.filter(
        pk__lt=OuterRef('pk'), fecha_inicio__lte=OuterRef('fecha_termino'), fecha_termino__gte=OuterRef('fecha_inicio'),
    )
    emails_repetidos = (
        Usuario.objects.exclude(email='').annotate(email_min=Lower('email'))
        .values('email_min').annotate(n=Count('id')).filter(n__gt=1).values('email_min')
    )
    runs_repetidos = (
        Usuario.objects.exclude(rut_anonimizado__isnull=True).exclude(rut_anonimizado='')
        .values('rut_anonimizado').annotate(n=Count('id')).filter(n__gt=1).values('rut_anonimizado')
    )
    ponderacion_incompleta = (
        ItemMedicion.objects.filter(activo=True).values('cargo_id', 'periodo_id')
        .annotate(suma=Sum('ponderador')).exclude(suma__gte=99.99, suma__lte=100.01)
    )

    return [
        # Identidad y acceso
        ('usuario.email_duplicado', CRITICO,
         'Usuarios que comparten el mismo email (el inicio de sesión por email es ambiguo)',
         Usuario.objects.annotate(email_min=Lower('email')).filter(email_min__in=emails_repetidos),
         'Dejar un email distinto por cuenta; luego agregar UNIQUE (fase 2).'),
        ('usuario.run_duplicado', ADVERTENCIA,
         'Funcionarios con el mismo RUN (posible persona registrada dos veces)',
         Usuario.objects.filter(rut_anonimizado__in=runs_repetidos),
         'Fusionar o desactivar la cuenta duplicada.'),
        ('usuario.delegado_sin_delegacion', ADVERTENCIA,
         'Delegados sin delegación asignada (no ven datos y no pueden validar)',
         Usuario.objects.filter(rol=Usuario.Rol.DELEGADO, delegacion__isnull=True, is_active=True),
         'Asignar la delegación en el mantenedor de funcionarios.'),
        ('usuario.rol_distinto_a_roles', INFO,
         'Usuarios cuyo rol principal no figura entre sus roles adicionales asignados',
         Usuario.objects.filter(roles__isnull=False).exclude(roles__nombre_rol=F('rol')).distinct(),
         'Unificar el sistema de roles (fase 4).'),

        # Períodos e ítems
        ('periodo.fechas_invertidas', CRITICO, 'Períodos cuya fecha de inicio es posterior a la de término',
         PeriodoMedicion.objects.filter(fecha_inicio__gt=F('fecha_termino')), 'Corregir las fechas del período.'),
        ('periodo.superpuesto', ADVERTENCIA,
         'Períodos que se superponen con otro (una actividad podría contar en ambos)',
         PeriodoMedicion.objects.filter(Exists(periodo_superpuesto)), 'Ajustar las fechas para que no se crucen.'),
        ('periodo.umbrales_invertidos', ADVERTENCIA, 'Períodos con umbral ámbar mayor o igual al verde',
         PeriodoMedicion.objects.filter(umbral_semaforo_ambar__gte=F('umbral_semaforo_verde')),
         'El umbral ámbar debe ser menor que el verde.'),
        ('periodo.umbral_legado_divergente', INFO,
         'Períodos cuyo umbral heredado (umbral_verde/ambar) no coincide con el vigente',
         PeriodoMedicion.objects.exclude(umbral_verde=F('umbral_semaforo_verde', ))
         | PeriodoMedicion.objects.exclude(umbral_ambar=F('umbral_semaforo_ambar')),
         'Se sincronizan al guardar el período; unificar columnas (fase 3).'),
        ('item.meta_no_positiva', CRITICO, 'Ítems con meta menor o igual a cero (división por cero en el cálculo)',
         ItemMedicion.objects.filter(meta_cuantitativa__lte=0), 'Definir una meta mayor que cero.'),
        ('item.ponderador_fuera_de_rango', ADVERTENCIA, 'Ítems con ponderador menor que 0 o mayor que 100',
         ItemMedicion.objects.filter(Q(ponderador__lt=0) | Q(ponderador__gt=100)), 'Corregir el ponderador.'),
        ('item.ponderacion_no_suma_100', ADVERTENCIA,
         'Combinaciones cargo × período cuyos ítems activos no suman 100 %',
         ponderacion_incompleta, 'Ajustar en "Configurar ponderaciones".', 'cargo_id'),

        # Actividades
        ('actividad.item_divergente', ADVERTENCIA,
         'Actividades donde las columnas duplicadas item e item_medicion apuntan a ítems distintos',
         Actividad.objects.filter(item__isnull=False, item_medicion__isnull=False).exclude(item_id=F('item_medicion_id')),
         'item_medicion es la columna vigente; unificar (fase 3).'),
        ('actividad.anulada_sin_trazabilidad', ADVERTENCIA, 'Actividades anuladas sin fecha o sin responsable de la anulación',
         Actividad.objects.filter(anulada=True).filter(Q(anulada_en__isnull=True) | Q(anulada_por__isnull=True)),
         'Completar desde la auditoría.'),
        ('actividad.enlace_compromiso_no_reciproco', ADVERTENCIA,
         'Actividades cuyo compromiso vinculado apunta a otra actividad',
         Actividad.objects.filter(compromiso_agenda__isnull=False).exclude(compromiso_agenda__actividad_id=F('id')),
         'Revisar el vínculo; la relación está duplicada en ambas tablas (fase 3).'),
        ('actividad.coordenadas_invalidas', ADVERTENCIA, 'Actividades con latitud o longitud fuera de rango',
         Actividad.objects.filter(Q(latitud__lt=-90) | Q(latitud__gt=90) | Q(longitud__lt=-180) | Q(longitud__gt=180)),
         'Corregir o eliminar las coordenadas.'),
        ('actividad.delegacion_distinta_al_funcionario', INFO,
         'Actividades registradas en una delegación distinta a la actual del funcionario',
         Actividad.objects.exclude(funcionario__delegacion__isnull=True).exclude(delegacion_id=F('funcionario__delegacion_id')),
         'Normal si el funcionario cambió de delegación; verificar si no.'),

        # Compromisos
        ('compromiso.estado_invalido', CRITICO, 'Compromisos con un estado fuera de la lista válida',
         CompromisoAgenda.objects.exclude(estado__in=estados_compromiso), 'Corregir el estado.'),
        ('compromiso.fechas_divergentes', ADVERTENCIA,
         'Compromisos donde fecha_compromiso y fecha_comprometida no coinciden',
         CompromisoAgenda.objects.exclude(fecha_compromiso=F('fecha_comprometida')),
         'fecha_comprometida es la vigente para plazos; unificar (fase 3).'),
        ('compromiso.realizado_sin_fecha', ADVERTENCIA,
         'Compromisos realizados sin fecha de realización (no se puede medir el plazo)',
         CompromisoAgenda.objects.filter(estado__in=realizados, fecha_realizacion__isnull=True),
         'Registrar la fecha de realización.'),
        ('compromiso.delegacion_distinta_a_actividad', ADVERTENCIA,
         'Compromisos en una delegación distinta a la de su actividad de origen',
         CompromisoAgenda.objects.filter(actividad__isnull=False).exclude(delegacion_id=F('actividad__delegacion_id')),
         'Revisar a qué territorio corresponde.'),
        ('compromiso.observacion_sin_compromiso', INFO, 'Observaciones de compromiso sin compromiso asociado',
         ObservacionCompromiso.objects.filter(compromiso__isnull=True), 'Cambiar on_delete a CASCADE o PROTECT (fase 2).'),

        # Evidencias
        ('evidencia.estado_invalido', CRITICO, 'Evidencias con un estado fuera de la lista válida',
         Evidencia.objects.exclude(estado__in=estados_evidencia), 'Corregir el estado.'),
        ('evidencia.validada_sin_trazabilidad', ADVERTENCIA,
         'Evidencias aprobadas o rechazadas sin verificador o sin fecha de validación',
         Evidencia.objects.exclude(estado=Evidencia.Estado.PENDIENTE)
         .filter(Q(verificador__isnull=True) | Q(fecha_validacion__isnull=True)),
         'Completar desde la auditoría.'),
        ('evidencia.autovalidada', ADVERTENCIA,
         'Evidencias validadas por la misma persona que registró la actividad o subió el archivo',
         Evidencia.objects.filter(verificador__isnull=False).filter(
             Q(verificador_id=F('actividad__funcionario_id')) | Q(verificador_id=F('subido_por_id'))
         ), 'Revisar: hoy el sistema lo impide, son casos anteriores a la regla.'),
        ('evidencia.sin_hash', INFO, 'Evidencias con archivo pero sin SHA-256 del contenido',
         Evidencia.objects.filter(Q(hash_sha256__isnull=True) | Q(hash_sha256=''))
         .filter(Q(archivo_foto__gt='') | Q(archivo_foto_2__gt='') | Q(archivo_foto_3__gt='')),
         'Cargas anteriores al control de integridad.'),

        # Ajustes de incentivo
        ('ajuste.estado_desconocido', ADVERTENCIA, 'Ajustes con un estado no reconocido',
         AjusteIncentivo.objects.exclude(estado__in=estados_ajuste + estados_ajuste_heredados),
         'Usar PENDIENTE, APROBADO o RECHAZADO.'),
        ('ajuste.estado_heredado', INFO, 'Ajustes con estados heredados (APLICADO, INCENTIVO, PENALIZACION)',
         AjusteIncentivo.objects.filter(estado__in=estados_ajuste_heredados),
         'Migrar a APROBADO; el estado no debe mezclarse con el tipo (fase 3).'),
        ('ajuste.tipo_contradice_concepto', ADVERTENCIA,
         'Ajustes cuyo tipo (incentivo/penalización) contradice la naturaleza del concepto',
         AjusteIncentivo.objects.filter(
             Q(tipo__in=tipos_positivos, concepto__naturaleza='NEGATIVO')
             | Q(tipo__in=tipos_negativos, concepto__naturaleza='POSITIVO')
         ), 'Corregir el tipo o el concepto.'),
        ('ajuste.decidido_sin_trazabilidad', ADVERTENCIA, 'Ajustes aprobados o rechazados sin responsable o sin fecha',
         AjusteIncentivo.objects.filter(estado__in=('APROBADO', 'RECHAZADO'))
         .filter(Q(aprobadoPor__isnull=True) | Q(fechaAprobacion__isnull=True)), 'Completar desde la auditoría.'),
        ('ajuste.autoaprobado', ADVERTENCIA, 'Ajustes aprobados por el propio funcionario beneficiado',
         AjusteIncentivo.objects.filter(aprobadoPor_id=F('funcionario_id')), 'Revisar la decisión.'),

        # Auditoría y operación
        ('auditoria.sin_entidad_o_accion', ADVERTENCIA, 'Registros de auditoría sin entidad o sin acción',
         RegistroAuditoria.objects.filter(Q(entidad='') | Q(accion='')), 'Registros anteriores a la validación de auditoría.'),
        ('auditoria.usuario_divergente', INFO,
         'Registros de auditoría donde las columnas duplicadas usuario y funcionario difieren',
         RegistroAuditoria.objects.filter(usuario__isnull=False, funcionario__isnull=False).exclude(usuario_id=F('funcionario_id')),
         'Se unifican al guardar desde la Fase A; unificar columnas (fase 3).'),
        ('operacion.indisponibilidad_invertida', ADVERTENCIA, 'Indisponibilidades cuyo fin es anterior o igual al inicio',
         Indisponibilidad.objects.filter(fin__lte=F('inicio')), 'Corregir el registro (afecta el informe de disponibilidad).'),
        ('legado.metas_e_indicadores', INFO, 'Registros en el sistema de metas heredado (MetaModel/Indicador)',
         MetaModel.objects.all(), 'Evaluar retirar el modelo heredado (fase 4).'),
        ('legado.indicadores', INFO, 'Registros en Indicador (sistema heredado)', Indicador.objects.all(), ''),
    ]


def verificar_coherencia():
    """Ejecuta todos los chequeos. Devuelve la lista de hallazgos con cantidad > 0."""
    hallazgos = chequear_claves_huerfanas()
    for codigo, severidad, descripcion, queryset, recomendacion, *campo in _chequeos_negocio():
        hallazgo = _hallazgo(codigo, severidad, descripcion, queryset, recomendacion, *(campo or ['pk']))
        if hallazgo.cantidad:
            hallazgos.append(hallazgo)
    hallazgos.sort(key=lambda h: (ORDEN_SEVERIDAD[h.severidad], h.codigo))
    return hallazgos


def total_chequeos():
    return len(_chequeos_negocio())
