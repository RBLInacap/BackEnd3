from datetime import timedelta
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from sgr_app.services.seguridad_acceso import obtener_ip_cliente
from sgr_app.concurrencia import ConcurrenciaContextMixin
from sgr_app.forms import (
    AreaForm,
    CatalogoServicioForm,
    CargoForm,
    ConceptoAjusteForm,
    DelegacionForm,
    FuncionarioForm,
    ItemPonderacionFormSet,
    MantenedorPeriodoForm,
    MantenedorSolicitanteForm,
    RolForm,
    SubatencionSocialForm,
    TipoActividadForm,
    TipoAtencionSocialForm,
)
from sgr_app.models import (
    Actividad,
    Area,
    Cargo,
    CatalogoServicio,
    CompromisoAgenda,
    ConceptoAjuste,
    Delegacion,
    ItemMedicion,
    PeriodoMedicion,
    RegistroAuditoria,
    Rol,
    Solicitante,
    SubatencionSocial,
    TipoActividad,
    TipoAtencionSocial,
    Usuario,
    VersionConfiguracionPeriodo,
)


class AdministradorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    allow_coordinador = False
    coordinador_can_manage = False

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (
            user.es_admin()
            or (self.allow_coordinador and user.es_coordinador)
        )


def _auditoria_periodo_snapshot(periodo):
    return {
        'nombre': periodo.nombre,
        'anio': periodo.anio,
        'trimestre': periodo.trimestre,
        'fecha_inicio': periodo.fecha_inicio.isoformat(),
        'fecha_termino': periodo.fecha_termino.isoformat(),
        'umbral_semaforo_verde': periodo.umbral_semaforo_verde,
        'umbral_semaforo_ambar': periodo.umbral_semaforo_ambar,
        'umbral_colectivo': periodo.umbral_colectivo,
        'tope_cumplimiento': periodo.tope_cumplimiento,
        'version_de_parametro': periodo.version_de_parametro,
        'version_cierre': periodo.version_cierre,
        'cerrado_en': periodo.cerrado_en.isoformat() if periodo.cerrado_en else None,
    }


def _snapshot_configuracion_periodo(periodo):
    # RF-038: incluye ítems, parámetros, catálogos y versión de reglas de cálculo.
    from sgr_app.services.configuracion import snapshot_configuracion_periodo

    return snapshot_configuracion_periodo(periodo)


def _guardar_version_configuracion_periodo(periodo, usuario):
    VersionConfiguracionPeriodo.objects.create(
        periodo=periodo,
        version=periodo.version_de_parametro,
        configuracion=_snapshot_configuracion_periodo(periodo),
        creado_por=usuario,
    )


def _auditoria_valor(instance):
    if isinstance(instance, PeriodoMedicion):
        return _auditoria_periodo_snapshot(instance)
    if isinstance(instance, ItemMedicion):
        return {
            'cargo_id': instance.cargo_id,
            'periodo_id': instance.periodo_id,
            'nombre_item': instance.nombre_item,
            'descripcion': instance.descripcion,
            'tipo_unidad': instance.tipo_unidad,
            'meta_cuantitativa': instance.meta_cuantitativa,
            'ponderador': instance.ponderador,
            'unidad_medida': instance.unidad_medida,
            'medir_compromisos': instance.medir_compromisos,
            'activo': instance.activo,
        }
    if isinstance(instance, ConceptoAjuste):
        return {
            'nombre': instance.nombre,
            'naturaleza': instance.naturaleza,
            'activo': instance.activo,
        }
    if isinstance(instance, Area):
        return {
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'estado': instance.estado,
        }
    if isinstance(instance, TipoActividad):
        return {
            'area_id': instance.area_id,
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'activo': instance.activo,
        }
    if isinstance(instance, TipoAtencionSocial):
        return {
            'area_id': instance.area_id,
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'activo': instance.activo,
        }
    if isinstance(instance, SubatencionSocial):
        return {
            'tipo_atencion_id': instance.tipo_atencion_id,
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'activo': instance.activo,
        }
    if isinstance(instance, Delegacion):
        return {
            'nombre': instance.nombre,
            'codigo': instance.codigo,
            'direccion': instance.direccion,
            'telefono': instance.telefono,
            'email': instance.email,
            'ambito': instance.ambito,
            'activo': instance.activo,
        }
    if isinstance(instance, Cargo):
        return {
            'codigo': instance.codigo,
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'area_id': instance.area_id,
            'activo': instance.activo,
        }
    if isinstance(instance, Solicitante):
        return {
            'run': instance.run,
            'nombre': instance.nombre,
            'apellido_paterno': instance.apellido_paterno,
            'apellido_materno': instance.apellido_materno,
            'tipo_solicitante': instance.tipo_solicitante,
            'telefono': instance.telefono,
            'correo': instance.correo,
            'estado': instance.estado,
        }
    if isinstance(instance, Usuario):
        return {
            'username': instance.username,
            'nombre': instance.first_name,
            'apellido': instance.last_name,
            'correo': instance.email,
            'rol': instance.rol,
            'roles': list(instance.roles.order_by('nombre_rol').values_list(
                'nombre_rol',
                flat=True,
            )),
            'area_id': instance.area_entidad_id,
            'cargo_id': instance.cargo_id,
            'delegacion_id': instance.delegacion_id,
            'activo': instance.is_active,
        }
    if isinstance(instance, Rol):
        return {
            'nombre_rol': instance.nombre_rol,
            'descripcion': instance.descripcion,
            'permisos': instance.permisos,
            'estado': instance.estado,
        }
    return str(instance)


def _siguiente_version_parametro(version_actual):
    try:
        mayor, menor = version_actual.split('.', 1)
        return f'{int(mayor)}.{int(menor) + 1}'
    except (AttributeError, TypeError, ValueError):
        return f'{version_actual}.1'


class AuditedSaveMixin(ConcurrenciaContextMixin):
    success_message = ''

    def form_valid(self, form):
        # RNF-003: se bloquea la fila y se rechaza si otra persona la modificó.
        with transaction.atomic():
            if self.hay_conflicto():
                return self.rechazar_por_conflicto(form)
            return self._form_valid_auditado(form)

    def _form_valid_auditado(self, form):
        previous_value = None
        previous_object = None
        if getattr(self, 'object', None) and self.object.pk:
            previous_object = type(self.object).objects.get(pk=self.object.pk)
            previous_value = _auditoria_valor(previous_object)
        with transaction.atomic():
            response = super().form_valid(form)
            if isinstance(self.object, ItemMedicion):
                item_fields = (
                    'cargo_id',
                    'periodo_id',
                    'nombre_item',
                    'descripcion',
                    'tipo_unidad',
                    'meta_cuantitativa',
                    'ponderador',
                    'unidad_medida',
                    'medir_compromisos',
                    'activo',
                )
                item_changed = previous_object is None or any(
                    getattr(previous_object, field) != getattr(self.object, field)
                    for field in item_fields
                )
                if item_changed:
                    periodo_ids = {self.object.periodo_id}
                    if previous_object and previous_object.periodo_id:
                        periodo_ids.add(previous_object.periodo_id)
                    for periodo_id in sorted(periodo_ids):
                        periodo = PeriodoMedicion.objects.select_for_update().get(
                            pk=periodo_id
                        )
                        anterior_periodo = _auditoria_periodo_snapshot(periodo)
                        periodo.version_de_parametro = _siguiente_version_parametro(
                            periodo.version_de_parametro
                        )
                        periodo.save(update_fields=['version_de_parametro'])
                        RegistroAuditoria.objects.create(
                            usuario=self.request.user,
                            funcionario=self.request.user,
                            evento='MODIFICACION',
                            accion='VERSIONAR_PARAMETROS_PERIODO',
                            entidad='PeriodoMedicion',
                            identificador=str(periodo.pk),
                            identificador_registro=str(periodo.pk),
                            valor_anterior=anterior_periodo,
                            valor_nuevo=_auditoria_periodo_snapshot(periodo),
                            ip_origen=obtener_ip_cliente(self.request) or None,
                        )
            if isinstance(self.object, PeriodoMedicion) and (
                previous_value is None
                or previous_value['version_de_parametro']
                != self.object.version_de_parametro
            ):
                _guardar_version_configuracion_periodo(
                    self.object,
                    self.request.user,
                )
            valor_nuevo = _auditoria_valor(self.object)
            RegistroAuditoria.objects.create(
                usuario=self.request.user,
                funcionario=self.request.user,
                evento='CREACION' if previous_value is None else 'MODIFICACION',
                accion='CREACION' if previous_value is None else 'MODIFICACION',
                entidad=self.model.__name__,
                identificador=str(self.object.pk),
                identificador_registro=str(self.object.pk),
                valor_anterior=(
                    previous_value
                    if previous_value is None or isinstance(previous_value, dict)
                    else {'registro': previous_value}
                ),
                valor_nuevo=(
                    valor_nuevo
                    if isinstance(valor_nuevo, dict)
                    else {'registro': valor_nuevo}
                ),
                ip_origen=obtener_ip_cliente(self.request) or None,
            )
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class ActivoListView(AdministradorRequiredMixin, ListView):
    paginate_by = 25
    search_fields = ()
    title = ''
    create_url = ''
    update_url_name = ''
    delete_url_name = ''
    supports_reactivation = False
    active_field = 'activo'
    reactivation_entity = ''

    def get_queryset(self):
        is_inactive_view = self.supports_reactivation and (
            self.request.GET.get('estado') == 'inactivas'
        )
        queryset = super().get_queryset().filter(
            **{self.active_field: not is_inactive_view},
        ).order_by('nombre')
        term = self.request.GET.get('q', '').strip()
        if term:
            search = Q()
            for field in self.search_fields:
                search |= Q(**{f'{field}__icontains': term})
            queryset = queryset.filter(search)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': self.title,
            'create_url': self.create_url,
            'update_url_name': self.update_url_name,
            'delete_url_name': self.delete_url_name,
            'search_term': self.request.GET.get('q', '').strip(),
            'supports_reactivation': self.supports_reactivation,
            'reactivation_entity': self.reactivation_entity,
            'show_inactive': (
                self.supports_reactivation
                and self.request.GET.get('estado') == 'inactivas'
            ),
            'can_manage': (
                self.request.user.es_admin()
                or (
                    self.allow_coordinador
                    and self.coordinador_can_manage
                    and self.request.user.es_coordinador
                )
            ),
        })
        return context


class SoftDeleteView(AdministradorRequiredMixin, DeleteView):
    success_url = reverse_lazy('mantenedor_delegaciones_list')
    success_message = 'Registro desactivado; se conservó su historial.'

    def get_advertencias(self):
        """CU-01/CU-03: dependencias activas que conviene revisar antes de desactivar."""
        return []

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = self.get_success_url()
        context['advertencias'] = self.get_advertencias()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        advertencias = self.get_advertencias()
        if advertencias and not request.POST.get('confirmar_dependencias'):
            # Con dependencias activas se exige confirmar expresamente el aviso.
            messages.error(
                request,
                'Marca la casilla para confirmar que revisaste las dependencias activas.',
            )
            return self.render_to_response(self.get_context_data(object=self.object))
        previous_value = str(self.object)
        with transaction.atomic():
            self.object.activo = False
            self.object.save(update_fields=['activo'])
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='ELIMINACION',
                accion='BAJA_LOGICA',
                entidad=self.model.__name__,
                identificador=str(self.object.pk),
                identificador_registro=str(self.object.pk),
                valor_anterior={'registro': previous_value, 'activo': True},
                valor_nuevo={
                    'registro': previous_value,
                    'activo': False,
                    **({'advertencias_confirmadas': advertencias} if advertencias else {}),
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(request, self.success_message)
        if advertencias:
            messages.warning(request, 'Recuerda reasignar: ' + ' '.join(advertencias))
        return HttpResponseRedirect(self.get_success_url())


class ActivoBooleanListView(AdministradorRequiredMixin, ListView):
    paginate_by = 25
    activo_field = ''
    ordering_field = 'pk'
    search_fields = ()
    title = ''
    create_url = ''
    update_url_name = ''
    delete_url_name = ''
    supports_reactivation = True
    reactivation_entity = ''

    def get_queryset(self):
        show_inactive = self.request.GET.get('estado') == 'inactivas'
        queryset = super().get_queryset().filter(
            **{self.activo_field: not show_inactive}
        )
        term = self.request.GET.get('q', '').strip()
        if term and self.search_fields:
            search = Q()
            for field in self.search_fields:
                search |= Q(**{f'{field}__icontains': term})
            queryset = queryset.filter(search)
        return queryset.order_by(self.ordering_field)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': self.title,
            'create_url': self.create_url,
            'update_url_name': self.update_url_name,
            'delete_url_name': self.delete_url_name,
            'search_term': self.request.GET.get('q', '').strip(),
            'supports_reactivation': self.supports_reactivation,
            'reactivation_entity': self.reactivation_entity,
            'show_inactive': (
                self.supports_reactivation
                and self.request.GET.get('estado') == 'inactivas'
            ),
            'can_manage': (
                self.request.user.es_admin()
                or (
                    self.allow_coordinador
                    and self.coordinador_can_manage
                    and self.request.user.es_coordinador
                )
            ),
        })
        return context


class AuditedSoftDeleteView(AdministradorRequiredMixin, DeleteView):
    activo_field = ''
    list_url = ''
    success_message = 'Registro desactivado; se conservó su historial.'

    def get_queryset(self):
        return super().get_queryset().filter(
            **{self.activo_field: True}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = self.list_url
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        previous_value = str(self.object)

        with transaction.atomic():
            setattr(self.object, self.activo_field, False)
            self.object.save(update_fields=[self.activo_field])
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='ELIMINACION',
                accion='BAJA_LOGICA',
                entidad=self.model.__name__,
                identificador=str(self.object.pk),
                identificador_registro=str(self.object.pk),
                valor_anterior={
                    'registro': previous_value,
                    self.activo_field: True,
                },
                valor_nuevo={
                    'registro': previous_value,
                    self.activo_field: False,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )

        messages.success(request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


REACTIVATABLE_RECORDS = {
    'delegaciones': (Delegacion, 'activo', 'mantenedor_delegaciones_list', False, False),
    'cargos': (Cargo, 'activo', 'mantenedor_cargos_list', False, False),
    'periodos': (PeriodoMedicion, 'estado', 'mantenedor_periodos_list', True, True),
    'solicitantes': (Solicitante, 'estado', 'mantenedor_solicitantes_list', True, True),
    'funcionarios': (Usuario, 'is_active', 'mantenedor_funcionarios_list', False, False),
    'roles': (Rol, 'estado', 'mantenedor_roles_list', False, False),
    'areas': (Area, 'estado', 'mantenedor_areas_list', False, False),
    'conceptos-ajuste': (
        ConceptoAjuste,
        'activo',
        'mantenedor_conceptos_ajuste_list',
        False,
        False,
    ),
    'catalogos': (CatalogoServicio, 'activo', 'mantenedor_items_list', True, True),
    'tipos-actividad': (
        TipoActividad,
        'activo',
        'mantenedor_tipos_actividad_list',
        False,
        False,
    ),
    'tipos-atencion': (
        TipoAtencionSocial,
        'activo',
        'mantenedor_tipos_atencion_list',
        False,
        False,
    ),
    'subatenciones': (
        SubatencionSocial,
        'activo',
        'mantenedor_subatenciones_list',
        False,
        False,
    ),
}


class ReactivateRecordView(AdministradorRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        record_config = REACTIVATABLE_RECORDS.get(kwargs['entity'])
        if not record_config:
            raise PermissionDenied
        (
            self.model,
            self.active_field,
            self.list_url_name,
            self.allow_coordinador,
            self.coordinador_can_manage,
        ) = record_config
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        record = get_object_or_404(
            self.model,
            pk=kwargs['pk'],
            **{self.active_field: False},
        )
        previous_value = str(record)
        with transaction.atomic():
            setattr(record, self.active_field, True)
            record.save(update_fields=[self.active_field])
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='CREACION',
                accion='REACTIVACION',
                entidad=self.model.__name__,
                identificador=str(record.pk),
                identificador_registro=str(record.pk),
                valor_anterior={
                    'registro': previous_value,
                    self.active_field: False,
                },
                valor_nuevo={
                    'registro': previous_value,
                    self.active_field: True,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(request, 'Registro reactivado correctamente.')
        return HttpResponseRedirect(
            f'{reverse_lazy(self.list_url_name)}?estado=inactivas'
        )


class DelegacionListView(ActivoListView):
    allow_coordinador = True
    supports_reactivation = True
    reactivation_entity = 'delegaciones'
    supports_reactivation = True
    model = Delegacion
    template_name = 'mantenedores/list.html'
    context_object_name = 'object_list'
    search_fields = ('nombre', 'codigo', 'email', 'ambito')
    title = 'Mantenedor de delegaciones'
    extra_context = {
        'secondary_column_label': 'Código',
        'detail_column_label': 'Teléfono',
        'is_cargo_list': False,
    }
    create_url = reverse_lazy('mantenedor_delegaciones_create')
    update_url_name = 'mantenedor_delegaciones_update'
    delete_url_name = 'mantenedor_delegaciones_delete'


class DelegacionCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = Delegacion
    form_class = DelegacionForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_delegaciones_list')
    success_message = 'Delegación creada correctamente.'
    extra_context = {
        'title': 'Crear delegación',
        'submit_label': 'Crear delegación',
        'cancel_url': reverse_lazy('mantenedor_delegaciones_list'),
    }


class DelegacionUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = Delegacion
    form_class = DelegacionForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_delegaciones_list')
    success_message = 'Delegación actualizada correctamente.'
    extra_context = {
        'title': 'Modificar delegación',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_delegaciones_list'),
    }

    def get_queryset(self):
        return Delegacion.objects.filter(activo=True)


class DelegacionDeleteView(SoftDeleteView):
    model = Delegacion
    template_name = 'mantenedores/confirm_delete.html'
    success_url = reverse_lazy('mantenedor_delegaciones_list')
    success_message = 'Delegación desactivada. El registro histórico se conservó.'

    def get_queryset(self):
        return Delegacion.objects.filter(activo=True)

    def get_advertencias(self):
        advertencias = []
        funcionarios = self.object.usuarios.filter(is_active=True).count()
        if funcionarios:
            advertencias.append(
                f'La delegación tiene {funcionarios} funcionario{"s" if funcionarios != 1 else ""} '
                f'activo{"s" if funcionarios != 1 else ""} asignado{"s" if funcionarios != 1 else ""}.'
            )
        abiertos = self.object.compromisos.filter(anulado=False).exclude(
            estado__in=(
                CompromisoAgenda.Estado.REALIZADO,
                CompromisoAgenda.Estado.COMPLETADO,
                CompromisoAgenda.Estado.CUMPLIDO,
            ),
        ).count()
        if abiertos:
            advertencias.append(
                f'Tiene {abiertos} compromiso{"s" if abiertos != 1 else ""} sin cerrar.'
            )
        return advertencias


class DelegacionReactivateView(AdministradorRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        delegacion = get_object_or_404(
            Delegacion,
            pk=kwargs['pk'],
            activo=False,
        )
        with transaction.atomic():
            delegacion.activo = True
            delegacion.save(update_fields=['activo'])
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='CREACION',
                accion='REACTIVACION',
                entidad='Delegacion',
                identificador=str(delegacion.pk),
                identificador_registro=str(delegacion.pk),
                valor_anterior={'registro': str(delegacion), 'activo': False},
                valor_nuevo={'registro': str(delegacion), 'activo': True},
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(request, 'Delegación reactivada correctamente.')
        return HttpResponseRedirect(
            f'{reverse_lazy("mantenedor_delegaciones_list")}?estado=inactivas'
        )


class CargoListView(ActivoListView):
    allow_coordinador = True
    supports_reactivation = True
    reactivation_entity = 'cargos'
    model = Cargo
    template_name = 'mantenedores/list.html'
    context_object_name = 'object_list'
    queryset = Cargo.objects.select_related('area')
    search_fields = ('codigo', 'nombre', 'descripcion', 'area__nombre')
    title = 'Mantenedor de cargos'
    extra_context = {
        'secondary_column_label': 'Código',
        'detail_column_label': 'Descripción',
        'third_column_label': 'Área',
        'is_cargo_list': True,
    }
    create_url = reverse_lazy('mantenedor_cargos_create')
    update_url_name = 'mantenedor_cargos_update'
    delete_url_name = 'mantenedor_cargos_delete'


class CargoCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = Cargo
    form_class = CargoForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_cargos_list')
    success_message = 'Cargo creado correctamente.'
    extra_context = {
        'title': 'Crear cargo',
        'submit_label': 'Crear cargo',
        'cancel_url': reverse_lazy('mantenedor_cargos_list'),
    }


class CargoUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = Cargo
    form_class = CargoForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_cargos_list')
    success_message = 'Cargo actualizado correctamente.'
    extra_context = {
        'title': 'Modificar cargo',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_cargos_list'),
    }

    def get_queryset(self):
        return Cargo.objects.filter(activo=True)


class CargoDeleteView(SoftDeleteView):
    model = Cargo
    template_name = 'mantenedores/confirm_delete.html'
    success_url = reverse_lazy('mantenedor_cargos_list')
    success_message = 'Cargo desactivado. El registro histórico se conservó.'

    def get_queryset(self):
        return Cargo.objects.filter(activo=True)

    def get_advertencias(self):
        advertencias = []
        funcionarios = self.object.usuarios.filter(is_active=True).count()
        if funcionarios:
            advertencias.append(
                f'El cargo tiene {funcionarios} funcionario{"s" if funcionarios != 1 else ""} '
                f'activo{"s" if funcionarios != 1 else ""} asignado{"s" if funcionarios != 1 else ""}.'
            )
        items = self.object.items_medicion.filter(
            activo=True, periodo__cerrado=False,
        ).count()
        if items:
            advertencias.append(
                f'Tiene {items} ítem{"s" if items != 1 else ""} de medición vigente{"s" if items != 1 else ""} '
                'en períodos abiertos.'
            )
        return advertencias


class ItemListView(ActivoBooleanListView):
    allow_coordinador = True
    coordinador_can_manage_catalog = True
    model = ItemMedicion
    queryset = ItemMedicion.objects.select_related('cargo', 'periodo')
    template_name = 'mantenedores/items_list.html'
    activo_field = 'activo'
    ordering_field = 'nombre_item'
    search_fields = ('nombre_item', 'descripcion', 'tipo_unidad')
    title = 'Mantenedor de ítems'
    create_url = reverse_lazy('mantenedor_ponderaciones_config')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        catalogo_form = kwargs.get('catalogo_form') or CatalogoServicioForm()
        user = self.request.user
        show_inactive = context['show_inactive']
        context.update({
            'catalogo_servicios': CatalogoServicio.objects.filter(
                activo=not show_inactive,
            ).order_by(
                'area', 'nombre',
            ),
            'catalogo_reactivation_entity': 'catalogos',
            'catalogo_form': catalogo_form,
            'catalogo_form_title': (
                'Modificar servicio' if catalogo_form.instance.pk else 'Agregar servicio'
            ),
            'can_manage_items': (
                user.es_admin() or user.es_coordinador
            ),
            'ponderaciones_url': reverse_lazy('mantenedor_ponderaciones_config'),
            'can_manage_catalog': (
                user.es_admin()
                or (self.coordinador_can_manage_catalog and user.es_coordinador)
            ),
        })
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        servicio_id = request.POST.get('catalogo_id')
        servicio = (
            get_object_or_404(CatalogoServicio, pk=servicio_id, activo=True)
            if servicio_id
            else None
        )

        if action == 'desactivar_catalogo':
            if not servicio:
                raise PermissionDenied
            valor_anterior = {
                'codigo': servicio.codigo,
                'nombre': servicio.nombre,
                'area': servicio.area,
                'activo': servicio.activo,
            }
            with transaction.atomic():
                servicio.activo = False
                servicio.save(update_fields=['activo'])
                RegistroAuditoria.objects.create(
                    usuario=request.user,
                    funcionario=request.user,
                    evento='ELIMINACION',
                    accion='BAJA_LOGICA',
                    entidad='CatalogoServicio',
                    identificador=str(servicio.pk),
                    identificador_registro=str(servicio.pk),
                    valor_anterior=valor_anterior,
                    valor_nuevo={**valor_anterior, 'activo': False},
                    ip_origen=obtener_ip_cliente(request) or None,
                )
            messages.success(request, 'Servicio desactivado; su historial se conservó.')
            return HttpResponseRedirect(self.request.path)

        if action != 'guardar_catalogo':
            raise PermissionDenied

        form = CatalogoServicioForm(request.POST, instance=servicio)
        if not form.is_valid():
            self.object_list = self.get_queryset()
            return self.render_to_response(
                self.get_context_data(catalogo_form=form)
            )

        valor_anterior = None
        if servicio:
            valor_anterior = {
                'codigo': servicio.codigo,
                'nombre': servicio.nombre,
                'area': servicio.area,
            }
        with transaction.atomic():
            if servicio:
                Actividad.objects.filter(
                    catalogo_servicio=servicio,
                    catalogo_servicio_nombre='',
                ).update(
                    catalogo_servicio_nombre=servicio.nombre,
                    catalogo_servicio_area=servicio.area or '',
                )
            servicio = form.save()
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='CREACION' if valor_anterior is None else 'MODIFICACION',
                accion='CREAR_CATALOGO_SERVICIO' if valor_anterior is None else 'ACTUALIZAR_CATALOGO_SERVICIO',
                entidad='CatalogoServicio',
                identificador=str(servicio.pk),
                identificador_registro=str(servicio.pk),
                valor_anterior=valor_anterior,
                valor_nuevo={
                    'codigo': servicio.codigo,
                    'nombre': servicio.nombre,
                    'area': servicio.area,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(request, 'Catálogo de servicios actualizado.')
        return HttpResponseRedirect(self.request.path)


class ConfigurarPonderacionesView(AdministradorRequiredMixin, View):
    # CU-10/CU-11 (HU-04, HU-05): el Coordinador define metas y funciones por cargo.
    allow_coordinador = True
    template_name = 'mantenedores/ponderaciones_form.html'
    success_message = 'La configuración del cargo y período suma exactamente 100%.'

    def _render(
        self,
        request,
        cargo=None,
        periodo=None,
        formset=None,
        error_message='',
    ):
        if formset is None and cargo and periodo:
            formset = ItemPonderacionFormSet(
                queryset=ItemMedicion.objects.filter(
                    cargo=cargo,
                    periodo=periodo,
                ).order_by('pk'),
                prefix='items',
            )
        total_actual = 0
        if cargo and periodo:
            total_actual = (
                ItemMedicion.objects.filter(
                    cargo=cargo,
                    periodo=periodo,
                    activo=True,
                ).aggregate(total=Sum('ponderador'))['total']
                or 0
            )
        return self.render_to_response(request, {
            'title': 'Configurar ponderaciones',
            'cargos': Cargo.objects.filter(activo=True).order_by('nombre'),
            'periodos': PeriodoMedicion.objects.filter(
                estado=True,
            ).order_by('-fecha_inicio'),
            'cargo': cargo,
            'periodo': periodo,
            'formset': formset,
            'total_actual': total_actual,
            'error_message': error_message,
            'periodo_cerrado': bool(periodo and periodo.cerrado),
            'periodos_futuros': self._periodos_futuros(periodo) if periodo else [],
            'periodos_futuros_seleccionados': {
                int(valor) for valor in request.POST.getlist('periodos_futuros') if valor.isdecimal()
            } if request.method == 'POST' else set(),
        })

    @staticmethod
    def _periodos_futuros(periodo):
        """CU-09: períodos abiertos posteriores al elegido, a los que se puede replicar."""
        return PeriodoMedicion.objects.filter(
            estado=True,
            cerrado=False,
            fecha_inicio__gt=periodo.fecha_inicio,
        ).order_by('fecha_inicio')

    def _replicar_configuracion(self, request, cargo, origen, destino):
        """CU-09 (HU-25): copia los ítems vigentes del cargo a un período futuro.

        Los ítems del destino con el mismo nombre se actualizan; los que no están en
        el origen se desactivan (nunca se borran). Sube la versión del destino y
        todo queda auditado.
        """
        destino = PeriodoMedicion.objects.select_for_update().get(pk=destino.pk)
        if destino.cerrado:
            raise ValidationError(f'El período {destino.nombre} está cerrado.')
        campos = (
            'descripcion', 'tipo_unidad', 'meta_cuantitativa', 'ponderador',
            'unidad_medida', 'medir_compromisos',
        )
        fuente = list(ItemMedicion.objects.filter(cargo=cargo, periodo=origen, activo=True))
        existentes = {
            item.nombre_item: item
            for item in ItemMedicion.objects.select_for_update().filter(cargo=cargo, periodo=destino)
        }
        anteriores = {item.pk: _auditoria_valor(item) for item in existentes.values()}
        previous_periodo = _auditoria_periodo_snapshot(destino)
        ip = obtener_ip_cliente(request) or None

        ItemMedicion.objects.filter(cargo=cargo, periodo=destino).update(activo=False)
        nombres_fuente = set()
        for original in fuente:
            nombres_fuente.add(original.nombre_item)
            item = existentes.get(original.nombre_item) or ItemMedicion(
                cargo=cargo, periodo=destino, nombre_item=original.nombre_item,
            )
            for campo in campos:
                setattr(item, campo, getattr(original, campo))
            item.activo = True
            es_nuevo = item.pk is None
            item.save()
            if es_nuevo or anteriores.get(item.pk) != _auditoria_valor(item):
                RegistroAuditoria.objects.create(
                    usuario=request.user,
                    funcionario=request.user,
                    evento='CREACION' if es_nuevo else 'MODIFICACION',
                    accion='REPLICAR_PONDERACION_ITEM',
                    entidad='ItemMedicion',
                    identificador=str(item.pk),
                    identificador_registro=str(item.pk),
                    valor_anterior=None if es_nuevo else anteriores.get(item.pk),
                    valor_nuevo={**_auditoria_valor(item), 'replicado_desde_periodo': origen.pk},
                    ip_origen=ip,
                )
        for nombre, item in existentes.items():
            if nombre not in nombres_fuente and anteriores[item.pk].get('activo'):
                item.refresh_from_db()
                RegistroAuditoria.objects.create(
                    usuario=request.user,
                    funcionario=request.user,
                    evento='MODIFICACION',
                    accion='REPLICAR_PONDERACION_ITEM',
                    entidad='ItemMedicion',
                    identificador=str(item.pk),
                    identificador_registro=str(item.pk),
                    valor_anterior=anteriores[item.pk],
                    valor_nuevo={**_auditoria_valor(item), 'replicado_desde_periodo': origen.pk},
                    ip_origen=ip,
                )

        destino.version_de_parametro = _siguiente_version_parametro(destino.version_de_parametro)
        destino.save(update_fields=['version_de_parametro'])
        RegistroAuditoria.objects.create(
            usuario=request.user,
            funcionario=request.user,
            evento='MODIFICACION',
            accion='VERSIONAR_PARAMETROS_PERIODO',
            entidad='PeriodoMedicion',
            identificador=str(destino.pk),
            identificador_registro=str(destino.pk),
            valor_anterior=previous_periodo,
            valor_nuevo={
                **_auditoria_periodo_snapshot(destino),
                'cargo_id': cargo.pk,
                'replicado_desde_periodo': origen.pk,
            },
            ip_origen=ip,
        )
        _guardar_version_configuracion_periodo(destino, request.user)
        return destino

    def render_to_response(self, request, context):
        from django.shortcuts import render

        return render(request, self.template_name, context)

    def _selected_group(self, request):
        source = request.POST if request.method == 'POST' else request.GET
        cargo_id = source.get('cargo')
        periodo_id = source.get('periodo')
        if not (
            cargo_id
            and cargo_id.isdecimal()
            and periodo_id
            and periodo_id.isdecimal()
        ):
            return None, None
        cargo = Cargo.objects.filter(pk=int(cargo_id), activo=True).first()
        periodo = PeriodoMedicion.objects.filter(
            pk=int(periodo_id),
            estado=True,
        ).first()
        return cargo, periodo

    def get(self, request, *args, **kwargs):
        cargo, periodo = self._selected_group(request)
        if (request.GET.get('cargo') or request.GET.get('periodo')) and (
            not cargo or not periodo
        ):
            return self._render(
                request,
                error_message='Selecciona un cargo y período vigentes.',
            )
        return self._render(request, cargo=cargo, periodo=periodo)

    def post(self, request, *args, **kwargs):
        cargo, periodo = self._selected_group(request)
        if not cargo or not periodo:
            return self._render(
                request,
                error_message='Selecciona un cargo y período vigentes.',
            )

        # CU-09: períodos futuros a los que también se aplica la configuración.
        ids_futuros = {
            int(valor) for valor in request.POST.getlist('periodos_futuros') if valor.isdecimal()
        }
        periodos_futuros = list(self._periodos_futuros(periodo).filter(pk__in=ids_futuros))
        if len(periodos_futuros) != len(ids_futuros):
            return self._render(
                request,
                cargo=cargo,
                periodo=periodo,
                error_message='Solo puedes replicar a períodos futuros abiertos.',
            )

        with transaction.atomic():
            periodo = PeriodoMedicion.objects.select_for_update().get(pk=periodo.pk)
            cargo = Cargo.objects.select_for_update().get(pk=cargo.pk)
            if periodo.cerrado:
                return self._render(
                    request,
                    cargo=cargo,
                    periodo=periodo,
                    error_message='No se puede modificar un período cerrado.',
                )

            items_queryset = ItemMedicion.objects.select_for_update().filter(
                cargo=cargo,
                periodo=periodo,
            ).order_by('pk')
            ids_esperados = set(items_queryset.values_list('pk', flat=True))
            formset = ItemPonderacionFormSet(
                request.POST,
                queryset=items_queryset,
                prefix='items',
            )
            if not formset.is_valid():
                return self._render(
                    request,
                    cargo=cargo,
                    periodo=periodo,
                    formset=formset,
                )

            ids_enviados = {
                form.instance.pk
                for form in formset.forms
                if form.instance.pk is not None
            }
            if ids_enviados != ids_esperados:
                return self._render(
                    request,
                    cargo=cargo,
                    periodo=periodo,
                    formset=formset,
                    error_message=(
                        'La lista de ítems cambió o está incompleta. '
                        'Recarga la página antes de guardar.'
                    ),
                )

            previous_items = {
                item.pk: _auditoria_valor(item)
                for item in ItemMedicion.objects.filter(
                    pk__in=ids_esperados,
                )
            }
            previous_periodo = _auditoria_periodo_snapshot(periodo)
            changed_forms = [form for form in formset.forms if form.has_changed()]
            if changed_forms:
                items_to_save = []
                changed_item_ids = set()
                for form in formset.forms:
                    if not form.instance.pk and not form.has_changed():
                        continue
                    item = form.save(commit=False)
                    item.cargo = cargo
                    item.periodo = periodo
                    item.ponderador = float(item.ponderador)
                    items_to_save.append(item)
                    if form.has_changed() and item.pk:
                        changed_item_ids.add(item.pk)

                items_queryset.update(activo=False)
                for item in items_queryset:
                    ItemMedicion.objects.filter(pk=item.pk).update(
                        nombre_item=f'__tmp_weight_{uuid4().hex}_{item.pk}'
                    )

                inactivos = [item for item in items_to_save if not item.activo]
                activos = sorted(
                    (item for item in items_to_save if item.activo),
                    key=lambda item: item.ponderador,
                )
                for item in inactivos + activos:
                    item.save()
                    if item.pk in changed_item_ids or item.pk not in previous_items:
                        RegistroAuditoria.objects.create(
                            usuario=request.user,
                            funcionario=request.user,
                            evento=(
                                'CREACION'
                                if item.pk not in previous_items
                                else 'MODIFICACION'
                            ),
                            accion='CONFIGURAR_PONDERACION_ITEM',
                            entidad='ItemMedicion',
                            identificador=str(item.pk),
                            identificador_registro=str(item.pk),
                            valor_anterior=previous_items.get(item.pk),
                            valor_nuevo=_auditoria_valor(item),
                            ip_origen=obtener_ip_cliente(request) or None,
                        )

                periodo.version_de_parametro = _siguiente_version_parametro(
                    periodo.version_de_parametro
                )
                periodo.save(update_fields=['version_de_parametro'])
                RegistroAuditoria.objects.create(
                    usuario=request.user,
                    funcionario=request.user,
                    evento='MODIFICACION',
                    accion='VERSIONAR_PARAMETROS_PERIODO',
                    entidad='PeriodoMedicion',
                    identificador=str(periodo.pk),
                    identificador_registro=str(periodo.pk),
                    valor_anterior=previous_periodo,
                    valor_nuevo=_auditoria_periodo_snapshot(periodo),
                    ip_origen=obtener_ip_cliente(request) or None,
                )
                _guardar_version_configuracion_periodo(periodo, request.user)

            replicados = []
            if periodos_futuros:
                total_vigente = (
                    ItemMedicion.objects.filter(cargo=cargo, periodo=periodo, activo=True)
                    .aggregate(total=Sum('ponderador'))['total']
                    or 0
                )
                if round(total_vigente, 2) != 100:
                    transaction.set_rollback(True)
                    return self._render(
                        request,
                        cargo=cargo,
                        periodo=periodo,
                        error_message=(
                            'Para replicar a otros períodos, la configuración vigente '
                            'debe sumar exactamente 100%.'
                        ),
                    )
                try:
                    for destino in periodos_futuros:
                        replicados.append(
                            self._replicar_configuracion(request, cargo, periodo, destino).nombre
                        )
                except ValidationError as error:
                    transaction.set_rollback(True)
                    return self._render(
                        request,
                        cargo=cargo,
                        periodo=periodo,
                        error_message='No se pudo replicar la configuración: ' + '; '.join(error.messages),
                    )

        messages.success(request, self.success_message)
        if replicados:
            messages.success(
                request,
                'La configuración también quedó vigente en: ' + ', '.join(replicados) + '.',
            )
        return HttpResponseRedirect(
            f'{reverse_lazy("mantenedor_ponderaciones_config")}'
            f'?cargo={cargo.pk}&periodo={periodo.pk}'
        )


class ItemConfigurationRedirectView(AdministradorRequiredMixin, View):
    allow_coordinador = True
    message = ''

    def get_redirect_response(self, request, **kwargs):
        item = None
        if kwargs.get('pk'):
            item = get_object_or_404(ItemMedicion, pk=kwargs['pk'])
        if item:
            target = (
                f'{reverse_lazy("mantenedor_ponderaciones_config")}'
                f'?cargo={item.cargo_id}&periodo={item.periodo_id}'
            )
        else:
            target = reverse_lazy('mantenedor_ponderaciones_config')
        messages.info(request, self.message)
        return HttpResponseRedirect(target)

    def get(self, request, *args, **kwargs):
        return self.get_redirect_response(request, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get_redirect_response(request, **kwargs)


class ItemCreateView(ItemConfigurationRedirectView):
    message = 'Los ítems y ponderaciones se guardan juntos desde la configuración del conjunto.'


class ItemUpdateView(ItemConfigurationRedirectView):
    message = 'Edita el ítem dentro de la configuración completa de su cargo y período.'


class ItemDeleteView(ItemConfigurationRedirectView):
    message = 'Desactiva el ítem dentro de la configuración completa de su cargo y período.'


class ConceptoAjusteListView(ActivoBooleanListView):
    model = ConceptoAjuste
    template_name = 'mantenedores/conceptos_ajuste_list.html'
    activo_field = 'activo'
    reactivation_entity = 'conceptos-ajuste'
    ordering_field = 'nombre'
    search_fields = ('nombre', 'naturaleza')
    title = 'Conceptos de ajuste'
    create_url = reverse_lazy('mantenedor_conceptos_ajuste_create')
    update_url_name = 'mantenedor_conceptos_ajuste_update'
    delete_url_name = 'mantenedor_conceptos_ajuste_delete'


class ConceptoAjusteCreateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    CreateView,
):
    model = ConceptoAjuste
    form_class = ConceptoAjusteForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_conceptos_ajuste_list')
    success_message = 'Concepto de ajuste creado correctamente.'
    extra_context = {
        'title': 'Crear concepto de ajuste',
        'submit_label': 'Crear concepto',
        'cancel_url': reverse_lazy('mantenedor_conceptos_ajuste_list'),
    }


class ConceptoAjusteUpdateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    UpdateView,
):
    model = ConceptoAjuste
    form_class = ConceptoAjusteForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_conceptos_ajuste_list')
    success_message = 'Concepto de ajuste actualizado correctamente.'
    extra_context = {
        'title': 'Modificar concepto de ajuste',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_conceptos_ajuste_list'),
    }

    def get_queryset(self):
        return ConceptoAjuste.objects.filter(activo=True)


class ConceptoAjusteDeleteView(AuditedSoftDeleteView):
    model = ConceptoAjuste
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'activo'
    list_url = reverse_lazy('mantenedor_conceptos_ajuste_list')
    success_url = reverse_lazy('mantenedor_conceptos_ajuste_list')
    success_message = 'Concepto desactivado; los ajustes históricos se conservaron.'


class PeriodoListView(ActivoBooleanListView):
    allow_coordinador = True
    coordinador_can_manage = True
    model = PeriodoMedicion
    template_name = 'mantenedores/periodos_list.html'
    activo_field = 'estado'
    reactivation_entity = 'periodos'
    ordering_field = '-fecha_inicio'
    search_fields = ('nombre',)
    title = 'Mantenedor de períodos'
    create_url = reverse_lazy('mantenedor_periodos_create')
    update_url_name = 'mantenedor_periodos_update'
    delete_url_name = 'mantenedor_periodos_delete'


class PeriodoCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    allow_coordinador = True
    model = PeriodoMedicion
    form_class = MantenedorPeriodoForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_periodos_list')
    success_message = 'Período creado correctamente.'
    extra_context = {
        'title': 'Crear período',
        'submit_label': 'Crear período',
        'cancel_url': reverse_lazy('mantenedor_periodos_list'),
    }


class PeriodoUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    allow_coordinador = True
    model = PeriodoMedicion
    form_class = MantenedorPeriodoForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_periodos_list')
    success_message = 'Período actualizado correctamente.'
    extra_context = {
        'title': 'Modificar período',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_periodos_list'),
    }

    def form_valid(self, form):
        anterior = PeriodoMedicion.objects.get(pk=self.object.pk)
        periodo = form.save(commit=False)
        campos_parametro = (
            'nombre',
            'anio',
            'trimestre',
            'fecha_inicio',
            'fecha_termino',
            'umbral_semaforo_verde',
            'umbral_semaforo_ambar',
            'umbral_colectivo',
            'tope_cumplimiento',
        )
        if any(
            getattr(anterior, campo) != getattr(periodo, campo)
            for campo in campos_parametro
        ):
            periodo.version_de_parametro = _siguiente_version_parametro(
                anterior.version_de_parametro
            )
        form.instance = periodo
        self.reabriendo = bool(anterior.cerrado and not periodo.cerrado)
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def _form_valid_auditado(self, form):
        response = super()._form_valid_auditado(form)
        if getattr(self, 'reabriendo', False):
            # CU-08 / RN-013: la reapertura queda auditada con su motivo.
            RegistroAuditoria.objects.create(
                usuario=self.request.user,
                funcionario=self.request.user,
                evento='MODIFICACION',
                accion='REABRIR_PERIODO',
                entidad='PeriodoMedicion',
                identificador=str(self.object.pk),
                identificador_registro=str(self.object.pk),
                valor_anterior={'cerrado': True},
                valor_nuevo={
                    'cerrado': False,
                    'motivo_reapertura': form.cleaned_data.get('motivo_reapertura', ''),
                },
                ip_origen=obtener_ip_cliente(self.request) or None,
            )
        return response

    def get_queryset(self):
        return PeriodoMedicion.objects.filter(estado=True)


class PeriodoDeleteView(AuditedSoftDeleteView):
    allow_coordinador = True
    model = PeriodoMedicion
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'estado'
    list_url = reverse_lazy('mantenedor_periodos_list')
    success_url = reverse_lazy('mantenedor_periodos_list')
    success_message = 'Período desactivado; se conservó su historial.'


class SolicitanteListView(ActivoBooleanListView):
    allow_coordinador = True
    coordinador_can_manage = True
    model = Solicitante
    template_name = 'mantenedores/solicitantes_list.html'
    activo_field = 'estado'
    reactivation_entity = 'solicitantes'
    ordering_field = 'nombre'
    search_fields = (
        'run',
        'nombre',
        'apellido_paterno',
        'apellido_materno',
        'correo',
    )
    title = 'Mantenedor de solicitantes'
    create_url = reverse_lazy('mantenedor_solicitantes_create')
    update_url_name = 'mantenedor_solicitantes_update'
    delete_url_name = 'mantenedor_solicitantes_delete'


class SolicitanteCreateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    CreateView,
):
    allow_coordinador = True
    model = Solicitante
    form_class = MantenedorSolicitanteForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_solicitantes_list')
    success_message = 'Solicitante creado correctamente.'
    extra_context = {
        'title': 'Crear solicitante',
        'submit_label': 'Crear solicitante',
        'cancel_url': reverse_lazy('mantenedor_solicitantes_list'),
    }


class SolicitanteUpdateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    UpdateView,
):
    allow_coordinador = True
    model = Solicitante
    form_class = MantenedorSolicitanteForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_solicitantes_list')
    success_message = 'Solicitante actualizado correctamente.'
    extra_context = {
        'title': 'Modificar solicitante',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_solicitantes_list'),
    }

    def get_queryset(self):
        return Solicitante.objects.filter(estado=True)


class SolicitanteDeleteView(AuditedSoftDeleteView):
    allow_coordinador = True
    model = Solicitante
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'estado'
    list_url = reverse_lazy('mantenedor_solicitantes_list')
    success_url = reverse_lazy('mantenedor_solicitantes_list')
    success_message = 'Solicitante desactivado; se conservó su historial.'


class FuncionarioListView(ActivoBooleanListView):
    allow_coordinador = True
    model = Usuario
    template_name = 'mantenedores/funcionarios_list.html'
    activo_field = 'is_active'
    reactivation_entity = 'funcionarios'
    ordering_field = 'last_name'
    search_fields = ('rut_anonimizado', 'first_name', 'last_name', 'email')
    title = 'Mantenedor de funcionarios'
    create_url = reverse_lazy('mantenedor_funcionarios_create')
    update_url_name = 'mantenedor_funcionarios_update'
    delete_url_name = 'mantenedor_funcionarios_delete'

    def get_queryset(self):
        return super().get_queryset().prefetch_related('roles')


class FuncionarioCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = Usuario
    form_class = FuncionarioForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_funcionarios_list')
    success_message = (
        'Funcionario creado correctamente. Debe iniciar sesión con su correo y '
        'su RUN sin puntos ni guion dentro de 10 minutos; al ingresar deberá '
        'crear y confirmar una contraseña nueva.'
    )
    extra_context = {
        'title': 'Crear funcionario',
        'submit_label': 'Crear funcionario',
        'cancel_url': reverse_lazy('mantenedor_funcionarios_list'),
    }

    def form_valid(self, form):
        run_inicial = form.cleaned_data['run'].replace('.', '').replace('-', '')
        form.instance.set_password(run_inicial)
        form.instance.must_change_password = True
        form.instance.initial_login_expires_at = (
            timezone.now() + timedelta(minutes=10)
        )
        return super().form_valid(form)


class FuncionarioRenewAccessView(AdministradorRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        funcionario = get_object_or_404(Usuario, pk=kwargs['pk'])
        if not funcionario.must_change_password:
            messages.error(request, 'Este funcionario ya completó su primer acceso.')
            return HttpResponseRedirect(reverse_lazy('mantenedor_funcionarios_list'))
        if not funcionario.rut_anonimizado:
            messages.error(request, 'El funcionario no tiene un RUN registrado para renovar el acceso.')
            return HttpResponseRedirect(reverse_lazy('mantenedor_funcionarios_list'))

        run_inicial = (
            funcionario.rut_anonimizado.replace('.', '').replace('-', '')
        )
        activo_anterior = funcionario.is_active
        fecha_anterior = funcionario.initial_login_expires_at
        with transaction.atomic():
            funcionario.set_password(run_inicial)
            funcionario.is_active = True
            funcionario.initial_login_expires_at = (
                timezone.now() + timedelta(minutes=10)
            )
            funcionario.save(update_fields=[
                'password',
                'is_active',
                'initial_login_expires_at',
            ])
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='MODIFICACION',
                accion='RENOVAR_ACCESO_FUNCIONARIO',
                entidad='Usuario',
                identificador=str(funcionario.pk),
                identificador_registro=str(funcionario.pk),
                valor_anterior={
                    'activo': activo_anterior,
                    'initial_login_expires_at': (
                        fecha_anterior.isoformat() if fecha_anterior else None
                    ),
                },
                valor_nuevo={
                    'activo': True,
                    'initial_login_expires_at': (
                        funcionario.initial_login_expires_at.isoformat()
                    ),
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )

        messages.success(
            request,
            'Acceso renovado por 10 minutos. El funcionario debe usar su correo '
            'y su RUN sin puntos ni guion como contraseña inicial.',
        )
        return HttpResponseRedirect(reverse_lazy('mantenedor_funcionarios_list'))


class FuncionarioUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = Usuario
    form_class = FuncionarioForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_funcionarios_list')
    success_message = 'Funcionario actualizado correctamente.'
    extra_context = {
        'title': 'Modificar funcionario',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_funcionarios_list'),
    }

    def get_queryset(self):
        return Usuario.objects.filter(is_active=True)


class FuncionarioDeleteView(AuditedSoftDeleteView):
    model = Usuario
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'is_active'
    list_url = reverse_lazy('mantenedor_funcionarios_list')
    success_url = reverse_lazy('mantenedor_funcionarios_list')
    success_message = 'Funcionario desactivado; se conservó su historial.'


class RolListView(ActivoBooleanListView):
    allow_coordinador = True
    model = Rol
    template_name = 'mantenedores/roles_list.html'
    activo_field = 'estado'
    reactivation_entity = 'roles'
    ordering_field = 'nombre_rol'
    search_fields = ('nombre_rol', 'descripcion')
    title = 'Mantenedor de roles'
    create_url = reverse_lazy('mantenedor_roles_create')
    update_url_name = 'mantenedor_roles_update'
    delete_url_name = 'mantenedor_roles_delete'


class RolCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = Rol
    form_class = RolForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_roles_list')
    success_message = 'Rol creado correctamente.'
    extra_context = {
        'title': 'Crear rol',
        'submit_label': 'Crear rol',
        'cancel_url': reverse_lazy('mantenedor_roles_list'),
    }


class RolUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = Rol
    form_class = RolForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_roles_list')
    success_message = 'Rol actualizado correctamente.'
    extra_context = {
        'title': 'Modificar rol',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_roles_list'),
    }

    def get_queryset(self):
        return Rol.objects.filter(estado=True)


class RolDeleteView(AuditedSoftDeleteView):
    model = Rol
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'estado'
    list_url = reverse_lazy('mantenedor_roles_list')
    success_url = reverse_lazy('mantenedor_roles_list')
    success_message = 'Rol desactivado; se conservó su historial.'


class AreaListView(ActivoBooleanListView):
    allow_coordinador = True
    model = Area
    template_name = 'mantenedores/areas_list.html'
    activo_field = 'estado'
    reactivation_entity = 'areas'
    ordering_field = 'nombre'
    search_fields = ('nombre', 'descripcion')
    title = 'Mantenedor de áreas'
    create_url = reverse_lazy('mantenedor_areas_create')
    update_url_name = 'mantenedor_areas_update'
    delete_url_name = 'mantenedor_areas_delete'


class AreaCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = Area
    form_class = AreaForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_areas_list')
    success_message = 'Área creada correctamente.'
    extra_context = {
        'title': 'Crear área',
        'submit_label': 'Crear área',
        'cancel_url': reverse_lazy('mantenedor_areas_list'),
    }


class AreaUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = Area
    form_class = AreaForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_areas_list')
    success_message = 'Área actualizada correctamente.'
    extra_context = {
        'title': 'Modificar área',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_areas_list'),
    }

    def get_queryset(self):
        return Area.objects.filter(estado=True)


class AreaDeleteView(AuditedSoftDeleteView):
    model = Area
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'estado'
    list_url = reverse_lazy('mantenedor_areas_list')
    success_url = reverse_lazy('mantenedor_areas_list')
    success_message = 'Área desactivada; se conservó su historial.'


class CatalogoAreaListView(ActivoBooleanListView):
    allow_coordinador = True
    template_name = 'mantenedores/catalogos_area_list.html'
    activo_field = 'activo'
    supports_reactivation = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['catalog_type'] = self.catalog_type
        return context


class TipoActividadListView(CatalogoAreaListView):
    model = TipoActividad
    reactivation_entity = 'tipos-actividad'
    ordering_field = 'area__nombre'
    search_fields = ('nombre', 'descripcion', 'area__nombre')
    title = 'Tipos de actividad'
    create_url = reverse_lazy('mantenedor_tipos_actividad_create')
    update_url_name = 'mantenedor_tipos_actividad_update'
    delete_url_name = 'mantenedor_tipos_actividad_delete'
    catalog_type = 'actividad'

    def get_queryset(self):
        return super().get_queryset().select_related('area')


class TipoActividadCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = TipoActividad
    form_class = TipoActividadForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_tipos_actividad_list')
    success_message = 'Tipo de actividad creado correctamente.'
    extra_context = {
        'title': 'Crear tipo de actividad',
        'submit_label': 'Crear tipo',
        'cancel_url': reverse_lazy('mantenedor_tipos_actividad_list'),
    }


class TipoActividadUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = TipoActividad
    form_class = TipoActividadForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_tipos_actividad_list')
    success_message = 'Tipo de actividad actualizado correctamente.'
    extra_context = {
        'title': 'Modificar tipo de actividad',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_tipos_actividad_list'),
    }

    def get_queryset(self):
        return TipoActividad.objects.filter(activo=True)


class TipoActividadDeleteView(AuditedSoftDeleteView):
    model = TipoActividad
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'activo'
    list_url = reverse_lazy('mantenedor_tipos_actividad_list')
    success_url = reverse_lazy('mantenedor_tipos_actividad_list')
    success_message = 'Tipo de actividad desactivado; se conservó el historial.'


class TipoAtencionSocialListView(CatalogoAreaListView):
    model = TipoAtencionSocial
    reactivation_entity = 'tipos-atencion'
    ordering_field = 'area__nombre'
    search_fields = ('nombre', 'descripcion', 'area__nombre')
    title = 'Tipos de atención social'
    create_url = reverse_lazy('mantenedor_tipos_atencion_create')
    update_url_name = 'mantenedor_tipos_atencion_update'
    delete_url_name = 'mantenedor_tipos_atencion_delete'
    catalog_type = 'atencion'

    def get_queryset(self):
        return super().get_queryset().select_related('area')


class TipoAtencionSocialCreateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    CreateView,
):
    model = TipoAtencionSocial
    form_class = TipoAtencionSocialForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_tipos_atencion_list')
    success_message = 'Tipo de atención social creado correctamente.'
    extra_context = {
        'title': 'Crear tipo de atención social',
        'submit_label': 'Crear tipo',
        'cancel_url': reverse_lazy('mantenedor_tipos_atencion_list'),
    }


class TipoAtencionSocialUpdateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    UpdateView,
):
    model = TipoAtencionSocial
    form_class = TipoAtencionSocialForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_tipos_atencion_list')
    success_message = 'Tipo de atención social actualizado correctamente.'
    extra_context = {
        'title': 'Modificar tipo de atención social',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_tipos_atencion_list'),
    }

    def get_queryset(self):
        return TipoAtencionSocial.objects.filter(activo=True)


class TipoAtencionSocialDeleteView(AuditedSoftDeleteView):
    model = TipoAtencionSocial
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'activo'
    list_url = reverse_lazy('mantenedor_tipos_atencion_list')
    success_url = reverse_lazy('mantenedor_tipos_atencion_list')
    success_message = 'Tipo de atención desactivado; se conservó el historial.'


class SubatencionSocialListView(CatalogoAreaListView):
    model = SubatencionSocial
    reactivation_entity = 'subatenciones'
    ordering_field = 'tipo_atencion__area__nombre'
    search_fields = ('nombre', 'descripcion', 'tipo_atencion__nombre', 'tipo_atencion__area__nombre')
    title = 'Subatenciones sociales'
    create_url = reverse_lazy('mantenedor_subatenciones_create')
    update_url_name = 'mantenedor_subatenciones_update'
    delete_url_name = 'mantenedor_subatenciones_delete'
    catalog_type = 'subatencion'

    def get_queryset(self):
        return super().get_queryset().select_related(
            'tipo_atencion',
            'tipo_atencion__area',
        )


class SubatencionSocialCreateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    CreateView,
):
    model = SubatencionSocial
    form_class = SubatencionSocialForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_subatenciones_list')
    success_message = 'Subatención social creada correctamente.'
    extra_context = {
        'title': 'Crear subatención social',
        'submit_label': 'Crear subatención',
        'cancel_url': reverse_lazy('mantenedor_subatenciones_list'),
    }


class SubatencionSocialUpdateView(
    AdministradorRequiredMixin,
    AuditedSaveMixin,
    UpdateView,
):
    model = SubatencionSocial
    form_class = SubatencionSocialForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_subatenciones_list')
    success_message = 'Subatención social actualizada correctamente.'
    extra_context = {
        'title': 'Modificar subatención social',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_subatenciones_list'),
    }

    def get_queryset(self):
        return SubatencionSocial.objects.filter(activo=True)


class SubatencionSocialDeleteView(AuditedSoftDeleteView):
    model = SubatencionSocial
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'activo'
    list_url = reverse_lazy('mantenedor_subatenciones_list')
    success_url = reverse_lazy('mantenedor_subatenciones_list')
    success_message = 'Subatención desactivada; se conservó el historial.'


class ReglasAlertaView(AdministradorRequiredMixin, View):
    """CU-07: el Administrador configura umbrales y destinatarios de cada alerta.

    Cada cambio crea una versión nueva con su vigencia (no se edita ni se borra
    la anterior) y queda en la auditoría.
    """

    template_name = 'mantenedores/reglas_alerta.html'

    def _contexto(self, errores=None):
        from sgr_app.models import ReglaAlerta
        from sgr_app.services.alertas import reglas_vigentes

        reglas = reglas_vigentes()
        etiquetas = dict(ReglaAlerta.Tipo.choices)
        return {
            'title': 'Reglas de alerta',
            'reglas': [
                {'regla': regla, 'etiqueta': etiquetas[tipo]}
                for tipo, regla in reglas.items()
            ],
            'destinatarios_opciones': ReglaAlerta.DESTINATARIOS,
            'historial': ReglaAlerta.objects.select_related('creada_por')[:30],
            'errores': errores or {},
            'ahora': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        }

    def get(self, request):
        return render(request, self.template_name, self._contexto())

    def post(self, request):
        from datetime import datetime

        from sgr_app.models import ReglaAlerta
        from sgr_app.services.alertas import REGLAS_POR_DEFECTO, reglas_vigentes

        tipo = request.POST.get('tipo', '')
        if tipo not in REGLAS_POR_DEFECTO:
            messages.error(request, 'Tipo de alerta no válido.')
            return redirect('mantenedor_reglas_alerta')
        errores = []
        activa = bool(request.POST.get('activa'))
        umbral_texto = (request.POST.get('umbral') or '').strip().replace(',', '.')
        umbral = None
        if umbral_texto:
            try:
                umbral = float(umbral_texto)
            except ValueError:
                errores.append('El umbral debe ser un número.')
            else:
                if umbral < 0:
                    errores.append('El umbral no puede ser negativo.')
                if tipo == 'RENDIMIENTO_BAJO' and umbral > 100:
                    errores.append('El umbral de rendimiento bajo es un porcentaje entre 0 y 100.')
        elif tipo != 'RENDIMIENTO_BAJO':
            errores.append('Indica el umbral de la regla.')
        validos = {codigo for codigo, _ in ReglaAlerta.DESTINATARIOS}
        destinatarios = [d for d in request.POST.getlist('destinatarios') if d in validos]
        if activa and not destinatarios:
            errores.append('Una regla activa necesita al menos un destinatario.')
        vigente_texto = (request.POST.get('vigente_desde') or '').strip()
        vigente_desde = timezone.now()
        if vigente_texto:
            try:
                vigente_desde = timezone.make_aware(datetime.fromisoformat(vigente_texto))
            except ValueError:
                errores.append('La fecha de vigencia no es válida.')
        if errores:
            return render(request, self.template_name, self._contexto({tipo: errores}), status=400)

        anterior = reglas_vigentes()[tipo]
        with transaction.atomic():
            version = ReglaAlerta.objects.create(
                tipo=tipo,
                activa=activa,
                umbral=umbral,
                destinatarios=destinatarios,
                vigente_desde=vigente_desde,
                creada_por=request.user,
            )
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='MODIFICACION',
                accion='CONFIGURAR_REGLA_ALERTA',
                entidad='ReglaAlerta',
                identificador=str(version.pk),
                identificador_registro=str(version.pk),
                valor_anterior={
                    'tipo': tipo,
                    'activa': anterior.activa,
                    'umbral': anterior.umbral,
                    'destinatarios': anterior.destinatarios,
                    'por_defecto': anterior.es_por_defecto,
                },
                valor_nuevo={
                    'tipo': tipo,
                    'activa': activa,
                    'umbral': umbral,
                    'destinatarios': destinatarios,
                    'vigente_desde': vigente_desde.isoformat(),
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(
            request,
            'Regla guardada como nueva versión; rige desde '
            f'{timezone.localtime(vigente_desde):%d/%m/%Y %H:%M}.',
        )
        return redirect('mantenedor_reglas_alerta')
