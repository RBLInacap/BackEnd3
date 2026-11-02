from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from sgr_app.forms import (
    AreaForm,
    CatalogoServicioForm,
    CargoForm,
    DelegacionForm,
    FuncionarioForm,
    MantenedorItemForm,
    MantenedorPeriodoForm,
    MantenedorSolicitanteForm,
    RolForm,
)
from sgr_app.models import (
    Actividad,
    Area,
    Cargo,
    CatalogoServicio,
    Delegacion,
    ItemMedicion,
    PeriodoMedicion,
    RegistroAuditoria,
    Rol,
    Solicitante,
    Usuario,
)


class AdministradorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    allow_supervisor = False
    supervisor_can_manage = False

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (
            user.is_superuser
            or user.rol == Usuario.Rol.ADMINISTRADOR
            or (self.allow_supervisor and user.es_supervisor)
        )


def _auditoria_periodo_snapshot(periodo):
    return {
        'nombre': periodo.nombre,
        'anio': periodo.anio,
        'trimestre': periodo.trimestre,
        'fecha_inicio': periodo.fecha_inicio.isoformat(),
        'fecha_termino': periodo.fecha_termino.isoformat(),
        'umbral_verde': periodo.umbral_verde,
        'umbral_ambar': periodo.umbral_ambar,
        'umbral_semaforo_verde': periodo.umbral_semaforo_verde,
        'umbral_semaforo_ambar': periodo.umbral_semaforo_ambar,
        'umbral_colectivo': periodo.umbral_colectivo,
        'tope_cumplimiento': periodo.tope_cumplimiento,
        'version_de_parametro': periodo.version_de_parametro,
    }


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
            'activo': instance.activo,
        }
    if isinstance(instance, Area):
        return {
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'estado': instance.estado,
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
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'departamento_id': instance.departamento_id,
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
            'es_delegado': instance.es_delegado,
            'area_id': instance.area_entidad_id,
            'cargo_id': instance.cargo_id,
            'delegacion_id': instance.delegacion_id,
            'activo': instance.is_active,
        }
    if isinstance(instance, Rol):
        return {
            'nombre_rol': instance.nombre_rol,
            'descripcion': instance.descripcion,
            'estado': instance.estado,
        }
    return str(instance)


def _siguiente_version_parametro(version_actual):
    try:
        mayor, menor = version_actual.split('.', 1)
        return f'{int(mayor)}.{int(menor) + 1}'
    except (AttributeError, TypeError, ValueError):
        return f'{version_actual}.1'


class AuditedSaveMixin:
    success_message = ''

    def form_valid(self, form):
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
                            ip_origen=self.request.META.get('REMOTE_ADDR'),
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
                ip_origen=self.request.META.get('REMOTE_ADDR'),
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

    def get_queryset(self):
        queryset = super().get_queryset().filter(activo=True).order_by('nombre')
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
            'can_manage': (
                self.request.user.is_superuser
                or self.request.user.rol == Usuario.Rol.ADMINISTRADOR
                or (
                    self.allow_supervisor
                    and self.supervisor_can_manage
                    and self.request.user.es_supervisor
                )
            ),
        })
        return context


class SoftDeleteView(AdministradorRequiredMixin, DeleteView):
    success_url = reverse_lazy('mantenedor_delegaciones_list')
    success_message = 'Registro desactivado; se conservó su historial.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = self.get_success_url()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
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
                valor_nuevo={'registro': previous_value, 'activo': False},
                ip_origen=request.META.get('REMOTE_ADDR'),
            )
        messages.success(request, self.success_message)
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

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            **{self.activo_field: True}
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
            'can_manage': (
                self.request.user.is_superuser
                or self.request.user.rol == Usuario.Rol.ADMINISTRADOR
                or (
                    self.allow_supervisor
                    and self.supervisor_can_manage
                    and self.request.user.es_supervisor
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
                ip_origen=request.META.get('REMOTE_ADDR'),
            )

        messages.success(request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class DelegacionListView(ActivoListView):
    allow_supervisor = True
    model = Delegacion
    template_name = 'mantenedores/list.html'
    context_object_name = 'object_list'
    search_fields = ('nombre', 'codigo', 'email', 'ambito')
    title = 'Mantenedor de delegaciones'
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


class CargoListView(ActivoListView):
    allow_supervisor = True
    model = Cargo
    template_name = 'mantenedores/list.html'
    context_object_name = 'object_list'
    queryset = Cargo.objects.select_related('departamento', 'departamento__delegacion')
    search_fields = ('nombre', 'descripcion', 'departamento__nombre_departamento')
    title = 'Mantenedor de cargos'
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


class ItemListView(ActivoBooleanListView):
    allow_supervisor = True
    supervisor_can_manage_catalog = True
    model = ItemMedicion
    queryset = ItemMedicion.objects.select_related('cargo', 'periodo')
    template_name = 'mantenedores/items_list.html'
    activo_field = 'activo'
    ordering_field = 'nombre_item'
    search_fields = ('nombre_item', 'descripcion', 'tipo_unidad')
    title = 'Mantenedor de ítems'
    create_url = reverse_lazy('mantenedor_items_create')
    update_url_name = 'mantenedor_items_update'
    delete_url_name = 'mantenedor_items_delete'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        catalogo_form = kwargs.get('catalogo_form') or CatalogoServicioForm()
        user = self.request.user
        context.update({
            'catalogo_servicios': CatalogoServicio.objects.filter(activo=True).order_by(
                'area', 'nombre'
            ),
            'catalogo_form': catalogo_form,
            'catalogo_form_title': (
                'Modificar servicio' if catalogo_form.instance.pk else 'Agregar servicio'
            ),
            'can_manage_items': (
                user.is_superuser or user.rol == Usuario.Rol.ADMINISTRADOR
            ),
            'can_manage_catalog': (
                user.is_superuser
                or user.rol == Usuario.Rol.ADMINISTRADOR
                or (self.supervisor_can_manage_catalog and user.es_supervisor)
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
                    ip_origen=request.META.get('REMOTE_ADDR'),
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
                ip_origen=request.META.get('REMOTE_ADDR'),
            )
        messages.success(request, 'Catálogo de servicios actualizado.')
        return HttpResponseRedirect(self.request.path)


class ItemCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = ItemMedicion
    form_class = MantenedorItemForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_items_list')
    success_message = 'Ítem creado correctamente.'
    extra_context = {
        'title': 'Crear ítem',
        'submit_label': 'Crear ítem',
        'cancel_url': reverse_lazy('mantenedor_items_list'),
    }


class ItemUpdateView(AdministradorRequiredMixin, AuditedSaveMixin, UpdateView):
    model = ItemMedicion
    form_class = MantenedorItemForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_items_list')
    success_message = 'Ítem actualizado correctamente.'
    extra_context = {
        'title': 'Modificar ítem',
        'submit_label': 'Guardar cambios',
        'cancel_url': reverse_lazy('mantenedor_items_list'),
    }

    def get_queryset(self):
        return ItemMedicion.objects.filter(activo=True)


class ItemDeleteView(AuditedSoftDeleteView):
    model = ItemMedicion
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'activo'
    list_url = reverse_lazy('mantenedor_items_list')
    success_url = reverse_lazy('mantenedor_items_list')
    success_message = 'Ítem desactivado; se conservó su historial.'


class PeriodoListView(ActivoBooleanListView):
    allow_supervisor = True
    model = PeriodoMedicion
    template_name = 'mantenedores/periodos_list.html'
    activo_field = 'estado'
    ordering_field = '-fecha_inicio'
    search_fields = ('nombre',)
    title = 'Mantenedor de períodos'
    create_url = reverse_lazy('mantenedor_periodos_create')
    update_url_name = 'mantenedor_periodos_update'
    delete_url_name = 'mantenedor_periodos_delete'


class PeriodoCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
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
            'umbral_verde',
            'umbral_ambar',
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
        return super().form_valid(form)

    def get_queryset(self):
        return PeriodoMedicion.objects.filter(estado=True)


class PeriodoDeleteView(AuditedSoftDeleteView):
    model = PeriodoMedicion
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'estado'
    list_url = reverse_lazy('mantenedor_periodos_list')
    success_url = reverse_lazy('mantenedor_periodos_list')
    success_message = 'Período desactivado; se conservó su historial.'


class SolicitanteListView(ActivoBooleanListView):
    allow_supervisor = True
    supervisor_can_manage = True
    model = Solicitante
    template_name = 'mantenedores/solicitantes_list.html'
    activo_field = 'estado'
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
    allow_supervisor = True
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
    allow_supervisor = True
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
    allow_supervisor = True
    model = Solicitante
    template_name = 'mantenedores/confirm_delete.html'
    activo_field = 'estado'
    list_url = reverse_lazy('mantenedor_solicitantes_list')
    success_url = reverse_lazy('mantenedor_solicitantes_list')
    success_message = 'Solicitante desactivado; se conservó su historial.'


class FuncionarioListView(ActivoBooleanListView):
    allow_supervisor = True
    model = Usuario
    template_name = 'mantenedores/funcionarios_list.html'
    activo_field = 'is_active'
    ordering_field = 'last_name'
    search_fields = ('rut_anonimizado', 'first_name', 'last_name', 'email')
    title = 'Mantenedor de funcionarios'
    create_url = reverse_lazy('mantenedor_funcionarios_create')
    update_url_name = 'mantenedor_funcionarios_update'
    delete_url_name = 'mantenedor_funcionarios_delete'


class FuncionarioCreateView(AdministradorRequiredMixin, AuditedSaveMixin, CreateView):
    model = Usuario
    form_class = FuncionarioForm
    template_name = 'mantenedores/form.html'
    success_url = reverse_lazy('mantenedor_funcionarios_list')
    success_message = 'Funcionario creado correctamente. La clave inicial es su RUN.'
    extra_context = {
        'title': 'Crear funcionario',
        'submit_label': 'Crear funcionario',
        'cancel_url': reverse_lazy('mantenedor_funcionarios_list'),
    }

    def form_valid(self, form):
        form.instance.set_password(form.cleaned_data['run'])
        return super().form_valid(form)


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
    allow_supervisor = True
    model = Rol
    template_name = 'mantenedores/roles_list.html'
    activo_field = 'estado'
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
    allow_supervisor = True
    model = Area
    template_name = 'mantenedores/areas_list.html'
    activo_field = 'estado'
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
