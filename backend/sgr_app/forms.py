from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.utils import timezone
from django.forms import modelformset_factory

from sgr_app.models import (
    Actividad,
    Area,
    AjusteIncentivo,
    Cargo,
    CasoSocial,
    CatalogoServicio,
    CompromisoAgenda,
    Delegacion,
    Evidencia,
    ItemMedicion,
    PeriodoMedicion,
    PERMISOS_ROL,
    Rol,
    Solicitante,
    SubatencionSocial,
    TipoActividad,
    TipoAtencionSocial,
    Usuario,
)
from sgr_app.validators import (
    normalize_chilean_phone,
    normalize_chilean_rut,
    validate_chilean_rut,
)

ACTIVIDAD_TERRITORIOS = (
    'Sector Centro', 'El Milagro', 'La Pampa', 'Las Compañías',
    'La Antena', 'Avenida del Mar', 'Sector Rural', 'Otro',
)
ACTIVIDAD_AREAS_APOYO = (
    'Operaciones y Territorio', 'Alumbrado Público', 'Aseo y Ornato',
    'DIDECO / Social', 'Tránsito y Transporte', 'Seguridad Ciudadana',
    'Medio Ambiente', 'SECPLAN / Proyectos',
)


class AjusteIncentivoForm(forms.Form):
    funcionario = forms.ModelChoiceField(
        queryset=Usuario.objects.none(),
        label='Funcionario',
    )
    tipo = forms.ChoiceField(
        choices=AjusteIncentivo.TIPO_CHOICES,
        label='Tipo de ajuste',
    )
    motivo = forms.CharField(
        label='Motivo',
        error_messages={'required': 'El motivo del ajuste es obligatorio.'},
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    valor_porcentaje = forms.FloatField(
        label='Porcentaje de ajuste',
        min_value=0.01,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
    )

    def __init__(self, *args, funcionarios=None, **kwargs):
        super().__init__(*args, **kwargs)
        if funcionarios is None:
            funcionarios = Usuario.objects.none()
        self.fields['funcionario'].queryset = funcionarios
        self.fields['funcionario'].widget.attrs.update({'class': 'form-select'})
        self.fields['tipo'].widget.attrs.update({'class': 'form-select'})
        self.fields['motivo'].widget.attrs.update({'class': 'form-control'})
        self.fields['valor_porcentaje'].widget.attrs.update({'class': 'form-control'})

    def clean_motivo(self):
        motivo = self.cleaned_data['motivo'].strip()
        if not motivo:
            raise ValidationError('El motivo del ajuste es obligatorio.')
        return motivo


class AjusteDecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=(
        ('APROBAR', 'Aprobar'),
        ('RECHAZAR', 'Rechazar'),
    ))
    observacion = forms.CharField(
        required=False,
        label='Motivo de rechazo',
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get('decision') == 'RECHAZAR'
            and not cleaned_data.get('observacion', '').strip()
        ):
            self.add_error('observacion', 'Indica el motivo del rechazo.')
        return cleaned_data


class CompromisoAgendaForm(forms.ModelForm):
    actividad = forms.ModelChoiceField(
        queryset=Actividad.objects.none(),
        label='Actividad asociada',
        required=False,
        empty_label='Sin actividad asociada',
    )
    fecha_comprometida = forms.DateField(
        label='Fecha comprometida',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    class Meta:
        model = CompromisoAgenda
        fields = (
            'solicitante',
            'origen',
            'territorio',
            'delegacion',
            'responsable',
            'fecha_comprometida',
            'actividad',
            'area_apoyo',
            'descripcion',
            'estado',
            'observacion',
        )
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'observacion': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, user, periodo, **kwargs):
        instance = kwargs.get('instance')
        data = kwargs.get('data') if 'data' in kwargs else (args[0] if args else None)
        if instance and instance.pk and data is not None:
            data = data.copy()
            aliases = {
                'delegacion': 'delegacion_id',
                'responsable': 'responsable_id',
                'observacion': 'observaciones',
                'actividad': 'actividad_id',
            }

            def set_value(name, value):
                if hasattr(data, 'setlist'):
                    data.setlist(name, [str(value or '')])
                else:
                    data[name] = value

            for name, alias in aliases.items():
                if name not in data and alias in data:
                    set_value(name, data[alias])
            for name in self.Meta.fields:
                if name in data:
                    continue
                value = getattr(instance, f'{name}_id', None) if name in (
                    'delegacion',
                    'responsable',
                    'actividad',
                ) else getattr(instance, name)
                set_value(name, value)
            if 'data' in kwargs:
                kwargs['data'] = data
            elif args:
                args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        self.user = user
        self.periodo = periodo
        self.fields['delegacion'].queryset = Delegacion.objects.filter(activo=True)
        self.fields['responsable'].queryset = Usuario.objects.filter(is_active=True)
        actividades = Actividad.objects.select_related(
            'funcionario', 'delegacion'
        ).all()
        if user.es_administrativo:
            if self.instance.pk:
                actividades = actividades.filter(
                    Q(funcionario=user) | Q(pk=self.instance.actividad_id)
                )
            else:
                actividades = actividades.filter(funcionario=user)
        elif user.esta_limitado_a_delegacion:
            if self.instance.pk:
                actividades = actividades.filter(
                    Q(delegacion_id=user.delegacion_id)
                    | Q(pk=self.instance.actividad_id)
                )
            else:
                actividades = actividades.filter(
                    delegacion_id=user.delegacion_id
                )

        if self.instance.pk:
            actividades = actividades.filter(
                Q(compromiso_agenda__isnull=True)
                | Q(compromiso_agenda_id=self.instance.pk),
                Q(compromisos__isnull=True)
                | Q(compromisos__pk=self.instance.pk),
            )
        else:
            actividades = actividades.filter(
                compromiso_agenda__isnull=True,
                compromisos__isnull=True,
            )
        self.fields['actividad'].queryset = actividades.distinct().order_by(
            '-fecha_actividad', '-pk'
        )
        self.fields['actividad'].label_from_instance = (
            lambda actividad: (
                f'#{actividad.pk} · {actividad.fecha_actividad:%d/%m/%Y} · '
                f'{actividad.actividad_solicitud or actividad.solicitud_problema or "Actividad"}'
            )
        )

        if self.instance.pk:
            self.initial['fecha_comprometida'] = (
                self.instance.fecha_comprometida or self.instance.fecha_compromiso
            )
            if self.instance.delegacion_id:
                self.fields['delegacion'].queryset = (
                    Delegacion.objects.filter(activo=True)
                    | Delegacion.objects.filter(pk=self.instance.delegacion_id)
                ).distinct()
            if self.instance.responsable_id:
                self.fields['responsable'].queryset = (
                    Usuario.objects.filter(is_active=True)
                    | Usuario.objects.filter(pk=self.instance.responsable_id)
                ).distinct()

        if user.es_administrativo:
            self.fields['delegacion'].queryset = Delegacion.objects.filter(
                pk=user.delegacion_id,
                activo=True,
            )
            self.fields['responsable'].queryset = Usuario.objects.filter(pk=user.pk)
        elif user.esta_limitado_a_delegacion:
            self.fields['delegacion'].queryset = Delegacion.objects.filter(
                pk=user.delegacion_id,
                activo=True,
            )
            self.fields['responsable'].queryset = Usuario.objects.filter(
                is_active=True,
                delegacion_id=user.delegacion_id,
            )
            if self.instance.responsable_id:
                self.fields['responsable'].queryset |= Usuario.objects.filter(
                    pk=self.instance.responsable_id,
                    delegacion_id=user.delegacion_id,
                )

        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )
        self.fields['estado'].choices = [
            (value, label)
            for value, label in CompromisoAgenda.ESTADO_CHOICES
            if value != 'VENCIDO'
        ]

    def clean(self):
        cleaned_data = super().clean()
        delegation = cleaned_data.get('delegacion')
        responsible = cleaned_data.get('responsable')
        committed_date = cleaned_data.get('fecha_comprometida')
        actividad = cleaned_data.get('actividad')

        if delegation and responsible and responsible.delegacion_id != delegation.pk:
            self.add_error(
                'responsable',
                'El responsable debe pertenecer a la delegación seleccionada.',
            )
        if self.user.esta_limitado_a_delegacion and delegation and delegation.pk != self.user.delegacion_id:
            self.add_error('delegacion', 'Solo puedes gestionar compromisos de tu delegación.')
        if self.user.es_administrativo and responsible and responsible.pk != self.user.pk:
            self.add_error('responsable', 'Solo puedes asignarte compromisos a ti mismo.')
        if actividad and delegation and actividad.delegacion_id != delegation.pk:
            self.add_error(
                'actividad',
                'La actividad seleccionada debe pertenecer a la delegación del compromiso.',
            )
        if actividad and (
            actividad.compromiso_agenda_id not in (None, self.instance.pk)
            or actividad.compromisos.exclude(pk=self.instance.pk).exists()
        ):
            self.add_error('actividad', 'La actividad ya está vinculada a otro compromiso.')
        if committed_date:
            if not self.periodo:
                self.add_error(
                    'fecha_comprometida',
                    'No hay un período trimestral activo para validar la fecha.',
                )
            elif not (
                self.periodo.fecha_inicio <= committed_date <= self.periodo.fecha_termino
            ):
                self.add_error(
                    'fecha_comprometida',
                    'La fecha debe estar dentro del período trimestral activo.',
                )
        if committed_date and actividad and committed_date < actividad.fecha_actividad:
            self.add_error(
                'fecha_comprometida',
                'La fecha comprometida no puede ser anterior a la fecha de la actividad.',
            )
        return cleaned_data

    def save(self, commit=True):
        compromiso = super().save(commit=False)
        compromiso.fecha_compromiso = self.cleaned_data['fecha_comprometida']
        compromiso.fecha_comprometida = self.cleaned_data['fecha_comprometida']
        compromiso.observaciones = compromiso.observacion
        if commit:
            compromisso.save()
            self.save_m2m()
        return compromiso


class CompromisoEstadoForm(forms.Form):
    estado = forms.ChoiceField(
        choices=(
            (CompromisoAgenda.Estado.PENDIENTE, 'Pendiente'),
            (CompromisoAgenda.Estado.EN_PROCESO, 'En proceso'),
            (CompromisoAgenda.Estado.REPROGRAMADO, 'Reprogramado'),
            (CompromisoAgenda.Estado.REALIZADO, 'Realizado'),
        ),
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'}),
    )
    observacion = forms.CharField(
        label='Observación del cambio',
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
    )

    def __init__(self, *args, compromiso, **kwargs):
        super().__init__(*args, **kwargs)
        self.compromiso = compromiso
        if compromiso.estado == CompromisoAgenda.Estado.REALIZADO:
            self.fields['estado'].choices = (
                (CompromisoAgenda.Estado.EN_PROCESO, 'Reabrir: En proceso'),
                (CompromisoAgenda.Estado.REALIZADO, 'Realizado'),
            )

    def clean_estado(self):
        estado = self.cleaned_data['estado']
        if estado == CompromisoAgenda.Estado.REALIZADO:
            actividad = self.compromiso.actividad
            evidencias = actividad.evidencias.all() if actividad else Evidencia.objects.none()
            if not evidencias.exists() or evidencias.exclude(
                estado=Evidencia.Estado.APROBADA
            ).exists():
                raise ValidationError(
                    'Para cerrar el compromiso, debe tener evidencia asociada y todas sus evidencias deben estar aprobadas.'
                )
        return estado

    def clean_observacion(self):
        observacion = self.cleaned_data['observacion'].strip()
        if not observacion:
            raise ValidationError('Explica el cambio de estado en la observación.')
        return observacion


class SolicitanteForm(forms.ModelForm):
    run = forms.CharField(
        required=True,
        max_length=20,
        error_messages={'required': 'El RUN es obligatorio.'},
    )
    nombre = forms.CharField(
        required=True,
        max_length=100,
        error_messages={'required': 'El nombre es obligatorio.'},
    )
    apellido_paterno = forms.CharField(
        required=True,
        max_length=100,
        error_messages={'required': 'El apellido paterno es obligatorio.'},
    )
    apellido_materno = forms.CharField(
        required=True,
        max_length=100,
        error_messages={'required': 'El apellido materno es obligatorio.'},
    )
    tipo_solicitante = forms.ChoiceField(
        required=True,
        choices=Solicitante.TIPO_SOLICITANTE_CHOICES,
        error_messages={'required': 'Selecciona un tipo de solicitante.'},
    )
    correo = forms.EmailField(
        required=True,
        max_length=254,
        error_messages={
            'required': 'El correo electrónico es obligatorio.',
            'invalid': 'Ingresa un correo electrónico válido.',
        },
    )
    telefono = forms.CharField(required=False, max_length=20)

    class Meta:
        model = Solicitante
        fields = (
            'run',
            'nombre',
            'apellido_paterno',
            'apellido_materno',
            'tipo_solicitante',
            'correo',
            'telefono',
        )

    def clean_run(self):
        value = self.cleaned_data['run']
        try:
            validate_chilean_rut(value)
            return normalize_chilean_rut(value)
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

    def clean_telefono(self):
        value = self.cleaned_data.get('telefono', '').strip()
        if not value:
            return ''
        try:
            return normalize_chilean_phone(value)
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

    def save(self, commit=True):
        solicitante = super().save(commit=False)
        solicitante.apellido = (
            f"{self.cleaned_data['apellido_paterno']} "
            f"{self.cleaned_data['apellido_materno']}"
        )
        if commit:
            solicitante.save()
            self.save_m2m()
        return solicitante


class CasoSocialForm(forms.ModelForm):
    fecha_apertura = forms.DateField(
        label='Fecha apertura',
        localize=False,
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(
            format='%Y-%m-%d',
            attrs={'type': 'date'},
        ),
    )

    class Meta:
        model = CasoSocial
        fields = ('codigo_caso', 'persona_anonimizada', 'delegacion', 'fecha_apertura')
        widgets = {
            'persona_anonimizada': forms.TextInput(attrs={
                'autocomplete': 'off',
                'placeholder': 'Código o identificación seudonimizada',
            }),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.is_bound:
            self.initial.setdefault('fecha_apertura', timezone.localdate())
        self.fields['delegacion'].queryset = Delegacion.objects.filter(activo=True)
        if user.esta_limitado_a_delegacion:
            self.fields['delegacion'].queryset = self.fields['delegacion'].queryset.filter(
                pk=user.delegacion_id,
            )
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )


class AtencionSocialForm(forms.Form):
    tipo_atencion_catalogo_id = forms.ModelChoiceField(
        queryset=TipoAtencionSocial.objects.none(),
        label='Tipo de atención',
    )
    subatencion_catalogo_id = forms.ModelChoiceField(
        queryset=SubatencionSocial.objects.none(),
        required=False,
        label='Subatención',
    )
    detalle = forms.CharField(
        label='Detalle',
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    resultado = forms.CharField(max_length=150, label='Resultado')
    fecha_gestion = forms.DateField(
        label='Fecha',
        error_messages={'required': 'La fecha de esta gestión es obligatoria.'},
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def __init__(self, *args, etapa, area_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.etapa = etapa
        tipos_atencion = TipoAtencionSocial.objects.filter(
            activo=True,
            area__estado=True,
        )
        if area_id:
            tipos_atencion = tipos_atencion.filter(area_id=area_id)
        tipos_atencion = tipos_atencion.order_by('nombre')
        self.fields['tipo_atencion_catalogo_id'].queryset = tipos_atencion

        selected_type_id = self.data.get(
            'tipo_atencion_catalogo_id',
            self.initial.get('tipo_atencion_catalogo_id'),
        )
        subatenciones = SubatencionSocial.objects.filter(
            activo=True,
            tipo_atencion__in=tipos_atencion,
        )
        if selected_type_id and str(selected_type_id).isdecimal():
            subatenciones = subatenciones.filter(
                tipo_atencion_id=int(selected_type_id)
            )
        else:
            subatenciones = subatenciones.none()
        self.fields['subatencion_catalogo_id'].queryset = subatenciones.order_by('nombre')
        etiquetas_fecha = {
            1: 'Fecha programada de visita',
            2: 'Fecha de visita realizada',
            3: 'Fecha de entrega del beneficio',
        }
        self.fields['fecha_gestion'].label = etiquetas_fecha.get(
            etapa,
            'Fecha de gestión',
        )
        if etapa == 1:
            self.fields['fecha_gestion'].widget.attrs['min'] = (
                timezone.localdate().isoformat()
            )
        elif etapa in (2, 3):
            self.fields['fecha_gestion'].widget.attrs['max'] = (
                timezone.localdate().isoformat()
            )
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned_data = super().clean()
        tipo_atencion = cleaned_data.get('tipo_atencion_catalogo_id')
        subatencion = cleaned_data.get('subatencion_catalogo_id')
        if tipo_atencion and subatencion:
            if subatencion.tipo_atencion_id != tipo_atencion.pk:
                self.add_error(
                    'subatencion_catalogo_id',
                    'Selecciona una subatención del tipo de atención elegido.',
                )
        elif tipo_atencion and tipo_atencion.subatenciones.filter(activo=True).exists():
            self.add_error(
                'subatencion_catalogo_id',
                'Selecciona una subatención para el tipo de atención elegido.',
            )
        return cleaned_data

    def clean_fecha_gestion(self):
        fecha_gestion = self.cleaned_data['fecha_gestion']
        hoy = timezone.localdate()
        if self.etapa == 1 and fecha_gestion < hoy:
            raise ValidationError(
                'La fecha programada de visita no puede ser anterior a hoy.'
            )
        if self.etapa in (2, 3) and fecha_gestion > hoy:
            raise ValidationError('La fecha de una gestión realizada no puede ser futura.')
        return fecha_gestion


class MiFichaPersonalForm(forms.ModelForm):
    email_confirmacion = forms.EmailField(
        label='Confirmar correo electrónico',
        required=True,
        max_length=254,
    )
    password_actual = forms.CharField(
        label='Contraseña actual',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )
    password_nueva = forms.CharField(
        label='Nueva contraseña',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Debe cumplir las reglas de seguridad configuradas para el sistema.',
    )
    password_confirmacion = forms.CharField(
        label='Confirmar nueva contraseña',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    class Meta:
        model = Usuario
        fields = ('first_name', 'last_name', 'email', 'telefono')
        labels = {
            'first_name': 'Nombre',
            'last_name': 'Apellido',
            'email': 'Correo electrónico',
            'telefono': 'Teléfono',
        }
        widgets = {
            'first_name': forms.TextInput(attrs={'autocomplete': 'given-name', 'maxlength': 150}),
            'last_name': forms.TextInput(attrs={'autocomplete': 'family-name', 'maxlength': 150}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email', 'maxlength': 254}),
            'telefono': forms.TextInput(attrs={
                'type': 'tel',
                'autocomplete': 'tel',
                'inputmode': 'numeric',
                'maxlength': 8,
                'pattern': '[0-9]{8}',
                'data-chilean-mobile': '',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if Usuario.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError('Este correo electrónico ya está asociado a otra cuenta.')
        return email

    def clean_telefono(self):
        phone = (self.cleaned_data.get('telefono') or '').strip()
        if not phone:
            return ''
        try:
            return normalize_chilean_phone(phone)
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        email_confirmacion = cleaned_data.get('email_confirmacion')
        if email and email_confirmacion and email.casefold() != email_confirmacion.strip().casefold():
            self.add_error('email_confirmacion', 'Los correos electrónicos no coinciden.')

        current_password = cleaned_data.get('password_actual')
        new_password = cleaned_data.get('password_nueva')
        password_confirmacion = cleaned_data.get('password_confirmacion')
        if current_password or new_password or password_confirmacion:
            if not current_password:
                self.add_error('password_actual', 'Ingresa tu contraseña actual para cambiarla.')
            elif not self.instance.check_password(current_password):
                self.add_error('password_actual', 'La contraseña actual no es correcta.')
            if not new_password:
                self.add_error('password_nueva', 'Ingresa la nueva contraseña.')
            if new_password and not password_confirmacion:
                self.add_error('password_confirmacion', 'Confirma la nueva contraseña.')
            elif new_password and new_password != password_confirmacion:
                self.add_error('password_confirmacion', 'Las nuevas contraseñas no coinciden.')
            if new_password and new_password == password_confirmacion:
                try:
                    validate_password(new_password, self.instance)
                except ValidationError as exc:
                    self.add_error('password_nueva', exc)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = self.cleaned_data.get('password_nueva')
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
            self.save_m2m()
        return user


class MantenedorModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class DelegacionForm(MantenedorModelForm):
    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono')
        if telefono and (not telefono.isascii() or not telefono.isdigit()):
            raise ValidationError('El teléfono de la delegación debe contener solo números.')
        return telefono

    class Meta:
        model = Delegacion
        fields = ('nombre', 'codigo', 'direccion', 'telefono', 'email', 'ambito')
        widgets = {
            'direccion': forms.TextInput(),
            'telefono': forms.TextInput(attrs={
                'type': 'tel',
                'inputmode': 'numeric',
                'pattern': '[0-9]*',
                'maxlength': 50,
                'data-digits-only': '',
            }),
            'email': forms.EmailInput(),
        }


class CargoForm(MantenedorModelForm):
    area = forms.ModelChoiceField(
        queryset=Area.objects.none(),
        label='Área',
        empty_label='Selecciona un área',
        help_text='Las opciones corresponden a las áreas activas del mantenedor de áreas.',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Cargo
        fields = ('nombre', 'descripcion', 'area')
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        areas = Area.objects.filter(estado=True).order_by('nombre')
        if self.instance.pk and self.instance.area_id:
            areas = (
                Area.objects.filter(pk=self.instance.area_id)
                | areas
            ).distinct()
        self.fields['area'].queryset = areas
        self.fields['area'].required = True
        self.fields['area'].widget.attrs['class'] = 'form-select'


class ItemPonderacionForm(forms.ModelForm):
    ponderador = forms.DecimalField(
        label='Ponderador (%)',
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'min': '0',
            'max': '100',
            'step': '0.01',
            'class': 'form-control',
        }),
    )

    class Meta:
        model = ItemMedicion
        fields = (
            'nombre_item',
            'descripcion',
            'tipo_unidad',
            'meta_cuantitativa',
            'ponderador',
            'unidad_medida',
            'activo',
        )
        labels = {
            'nombre_item': 'Nombre',
            'tipo_unidad': 'Tipo de unidad',
            'meta_cuantitativa': 'Meta cuantitativa',
            'unidad_medida': 'Unidad de medida',
            'activo': 'Vigente',
        }
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'tipo_unidad': forms.Select(attrs={'class': 'form-select'}),
            'meta_cuantitativa': forms.NumberInput(attrs={
                'min': '0.01',
                'step': '0.01',
                'class': 'form-control',
            }),
            'unidad_medida': forms.TextInput(attrs={'class': 'form-control'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )

    def _post_clean(self):
        self.instance._validar_suma_ponderador = False
        super()._post_clean()


class BaseItemPonderacionFormSet(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        ponderador_total = sum(
            (
                Decimal(str(form.cleaned_data['ponderador']))
                for form in self.forms
                if form.cleaned_data and form.cleaned_data.get('activo')
            ),
            Decimal('0'),
        )
        if ponderador_total != Decimal('100'):
            raise ValidationError(
                'La suma de los ponderadores vigentes debe ser exactamente 100%. '
                f'La suma ingresada es {ponderador_total:.2f}%.'
            )


ItemPonderacionFormSet = modelformset_factory(
    ItemMedicion,
    form=ItemPonderacionForm,
    formset=BaseItemPonderacionFormSet,
    extra=1,
)


class MantenedorPeriodoForm(MantenedorModelForm):
    class Meta:
        model = PeriodoMedicion
        fields = (
            'nombre',
            'anio',
            'trimestre',
            'fecha_inicio',
            'fecha_termino',
            'dias_computables',
            'umbral_semaforo_verde',
            'umbral_semaforo_ambar',
            'umbral_colectivo',
            'tope_cumplimiento',
            'version_de_parametro',
            'cerrado',
            'estado',
        )
        labels = {
            'anio': 'Año',
            'trimestre': 'Trimestre',
            'fecha_inicio': 'Inicio del período',
            'fecha_termino': 'Término del período',
            'dias_computables': 'Días computables',
            'umbral_semaforo_verde': 'Semáforo verde respecto al objetivo (%)',
            'umbral_semaforo_ambar': 'Semáforo ámbar respecto al objetivo (%)',
            'umbral_colectivo': 'Umbral colectivo (%)',
            'tope_cumplimiento': 'Tope de cumplimiento (%)',
            'version_de_parametro': 'Versión de parámetros',
            'cerrado': 'Período cerrado',
            'estado': 'Activo',
        }
        widgets = {
            'fecha_inicio': forms.DateInput(attrs={'type': 'date'}),
            'fecha_termino': forms.DateInput(attrs={'type': 'date'}),
            'umbral_semaforo_verde': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'umbral_semaforo_ambar': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'umbral_colectivo': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'tope_cumplimiento': forms.NumberInput(attrs={'step': '0.1', 'min': '0.1'}),
            'cerrado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'estado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version_de_parametro'].disabled = True
        today = timezone.localdate()
        try:
            maximum_date = today.replace(year=today.year + 1)
        except ValueError:
            maximum_date = today.replace(year=today.year + 1, day=28)

        if not self.instance.pk and not self.is_bound:
            self.initial.setdefault('fecha_inicio', today)
            self.initial.setdefault('fecha_termino', today)
            self.initial.setdefault('anio', today.year)
            self.initial.setdefault('trimestre', (today.month - 1) // 3 + 1)
            self.initial.setdefault('dias_computables', 1)

        for field_name in ('fecha_inicio', 'fecha_termino'):
            minimum_date = today
            maximum_field_date = maximum_date
            current_value = getattr(self.instance, field_name, None)
            if self.instance.pk and current_value:
                minimum_date = min(minimum_date, current_value)
                maximum_field_date = max(maximum_field_date, current_value)
            self.fields[field_name].widget.attrs.update({
                'min': minimum_date.isoformat(),
                'max': maximum_field_date.isoformat(),
            })

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and self.instance.cerrado:
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
                campo in cleaned_data
                and cleaned_data[campo] != getattr(self.instance, campo)
                for campo in campos_parametro
            ):
                self.add_error(
                    None,
                    'No se pueden modificar los parámetros de un período cerrado. '
                    'Reabra el período antes de editar su configuración.',
                )

        fecha_inicio = cleaned_data.get('fecha_inicio')
        fecha_termino = cleaned_data.get('fecha_termino')
        anio = cleaned_data.get('anio')
        trimestre = cleaned_data.get('trimestre')
        today = timezone.localdate()
        try:
            maximum_date = today.replace(year=today.year + 1)
        except ValueError:
            maximum_date = today.replace(year=today.year + 1, day=28)

        for field_name, value in (
            ('fecha_inicio', fecha_inicio),
            ('fecha_termino', fecha_termino),
        ):
            previous_value = (
                getattr(self.instance, field_name)
                if self.instance.pk
                else None
            )
            if (
                value
                and value != previous_value
                and not today <= value <= maximum_date
            ):
                self.add_error(
                    field_name,
                    f'La fecha debe estar entre {today:%d/%m/%Y} y '
                    f'{maximum_date:%d/%m/%Y}.',
                )

        if fecha_inicio and fecha_termino and fecha_termino < fecha_inicio:
            self.add_error(
                'fecha_termino',
                'La fecha de término no puede ser anterior a la de inicio.',
            )

        if fecha_inicio:
            anio_esperado = fecha_inicio.year
            trimestre_esperado = (fecha_inicio.month - 1) // 3 + 1
            if anio is not None and anio != anio_esperado:
                self.add_error('anio', 'El año debe coincidir con la fecha de inicio.')
            if trimestre is not None and trimestre != trimestre_esperado:
                self.add_error(
                    'trimestre',
                    'El trimestre debe coincidir con la fecha de inicio.',
                )

        if fecha_inicio and fecha_termino and fecha_inicio.year != fecha_termino.year:
            self.add_error(
                'fecha_termino',
                'Las fechas del período deben pertenecer al mismo año.',
            )

        return cleaned_data

    def save(self, commit=True):
        periodo = super().save(commit=False)
        periodo.anio = periodo.fecha_inicio.year
        periodo.trimestre = (periodo.fecha_inicio.month - 1) // 3 + 1
        periodo.dias_computables = max(
            (periodo.fecha_termino - periodo.fecha_inicio).days + 1,
            1,
        )
        if commit:
            periodo.save()
            self.save_m2m()
        return periodo


class MantenedorSolicitanteForm(MantenedorModelForm):
    class Meta:
        model = Solicitante
        fields = (
            'run',
            'nombre',
            'apellido_paterno',
            'apellido_materno',
            'tipo_solicitante',
            'telefono',
            'correo',
            'estado',
        )
        labels = {
            'run': 'RUN',
            'apellido_paterno': 'Apellido paterno',
            'apellido_materno': 'Apellido materno',
            'tipo_solicitante': 'Tipo de solicitante',
            'correo': 'Correo electrónico',
            'estado': 'Activo',
        }
        widgets = {
            'estado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def save(self, commit=True):
        solicitante = super().save(commit=False)
        solicitante.apellido = ' '.join(filter(None, (
            solicitante.apellido_paterno.strip(),
            solicitante.apellido_materno.strip(),
        )))
        if commit:
            solicitante.save()
            self.save_m2m()
        return solicitante


class FuncionarioForm(MantenedorModelForm):
    ROL_CHOICES = (
        ('ADMINISTRADOR', 'Administrador'),
        ('COORDINADOR', 'Coordinador'),
        ('DELEGADO', 'Delegado'),
        ('FUNCIONARIO', 'Funcionario'),
    )
    ROL_A_MODELO = {
        'ADMINISTRADOR': (Usuario.Rol.ADMINISTRADOR, False),
        'COORDINADOR': (Usuario.Rol.SUPERVISOR, False),
        'DELEGADO': (Usuario.Rol.SUPERVISOR, True),
        'FUNCIONARIO': (Usuario.Rol.ADMINISTRATIVO, False),
    }

    run = forms.CharField(label='RUN', max_length=20, required=True)
    nombre = forms.CharField(label='Nombre', max_length=150, required=True)
    apellidoPaterno = forms.CharField(
        label='Apellido paterno',
        max_length=150,
        required=True,
    )
    apellidoMaterno = forms.CharField(
        label='Apellido materno',
        max_length=150,
        required=True,
    )
    correo = forms.EmailField(label='Correo', max_length=254, required=True)
    rol = forms.ChoiceField(choices=ROL_CHOICES, initial='FUNCIONARIO')
    area_entidad = forms.ModelChoiceField(
        queryset=Area.objects.filter(estado=True).order_by('nombre'),
        required=True,
        label='Área',
        empty_label='Selecciona un área',
    )
    cargo = forms.ModelChoiceField(
        queryset=Cargo.objects.filter(activo=True).order_by('nombre'),
        required=False,
        label='Cargo',
    )
    delegacion = forms.ModelChoiceField(
        queryset=Delegacion.objects.filter(activo=True).order_by('nombre'),
        required=False,
        label='Delegación',
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Rol.objects.filter(estado=True).order_by('nombre_rol'),
        required=False,
        label='Roles adicionales',
        help_text='Puedes asignar más de un rol. Sus permisos se suman al rol principal.',
    )
    estado = forms.BooleanField(label='Activo', required=False)

    class Meta:
        model = Usuario
        fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ('area_entidad', 'cargo', 'delegacion', 'roles'):
            self.fields[field_name].widget.attrs['class'] = 'form-select'
        self.fields['estado'].widget.attrs['class'] = 'form-check-input'

        if self.instance.pk:
            roles_actuales = self.instance.roles.all()
            self.fields['roles'].queryset = (
                Rol.objects.filter(estado=True) | roles_actuales
            ).distinct().order_by('nombre_rol')
            run = self.instance.rut_anonimizado or ''
            apellidos = (self.instance.last_name or '').split(maxsplit=1)
            self.initial.update({
                'run': run,
                'nombre': self.instance.first_name,
                'apellidoPaterno': apellidos[0] if apellidos else '',
                'apellidoMaterno': apellidos[1] if len(apellidos) > 1 else '',
                'correo': self.instance.email,
                'estado': self.instance.is_active,
                'roles': roles_actuales,
                'rol': (
                    'DELEGADO'
                    if self.instance.es_delegado
                    else 'COORDINADOR'
                    if self.instance.rol == Usuario.Rol.SUPERVISOR
                    else 'ADMINISTRADOR'
                    if self.instance.rol == Usuario.Rol.ADMINISTRADOR
                    else 'FUNCIONARIO'
                ),
            })
        else:
            self.fields['estado'].initial = True

    def clean_run(self):
        try:
            run = normalize_chilean_rut(self.cleaned_data['run'])
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

        duplicates = Usuario.objects.filter(rut_anonimizado__iexact=run)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError('Ya existe un funcionario con ese RUN.')
        return run

    def clean_correo(self):
        correo = self.cleaned_data['correo'].strip().lower()
        duplicates = Usuario.objects.filter(email__iexact=correo)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError('Ya existe un usuario con ese correo.')
        return correo

    def clean(self):
        cleaned_data = super().clean()
        rol = cleaned_data.get('rol')
        delegacion = cleaned_data.get('delegacion')
        roles = cleaned_data.get('roles', ())
        requiere_delegacion = (
            rol == 'DELEGADO'
            or any(role.tienePermiso('GESTIONAR_DELEGACION') for role in roles)
        )
        if requiere_delegacion and not delegacion:
            self.add_error(
                'delegacion',
                'El rol Delegado o de gestión territorial requiere una delegación asignada.',
            )
        return cleaned_data

    def save(self, commit=True):
        funcionario = self.instance
        run = self.cleaned_data['run']
        funcionario.username = self.cleaned_data['correo']
        funcionario.rut_anonimizado = run
        funcionario.first_name = self.cleaned_data['nombre'].strip()
        funcionario.last_name = ' '.join(filter(None, (
            self.cleaned_data['apellidoPaterno'].strip(),
            self.cleaned_data['apellidoMaterno'].strip(),
        )))
        funcionario.email = self.cleaned_data['correo']
        funcionario.rol, funcionario.es_delegado = self.ROL_A_MODELO[
            self.cleaned_data['rol']
        ]
        funcionario.area_entidad = self.cleaned_data['area_entidad']
        funcionario.cargo = self.cleaned_data['cargo']
        funcionario.delegacion = self.cleaned_data['delegacion']
        funcionario.is_active = self.cleaned_data['estado']
        if commit:
            funcionario.save()
            self._save_m2m()
        else:
            self.save_m2m = self._save_m2m
        return funcionario


class InitialPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        label='Nueva contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )
    new_password2 = forms.CharField(
        label='Confirmar nueva contraseña',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('new_password1')
        password2 = cleaned_data.get('new_password2')
        if password1 and password2:
            if password1 != password2:
                self.add_error('new_password2', 'Las contraseñas no coinciden.')
            else:
                try:
                    validate_password(password1, user=self.user)
                except ValidationError as exc:
                    self.add_error('new_password1', exc)
        return cleaned_data


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'autocomplete': 'email',
        }),
    )


class PasswordResetCodeForm(forms.Form):
    code = forms.CharField(
        label='Código de verificación',
        max_length=64,
        widget=forms.TextInput(attrs={
            'class': 'form-control text-center font-monospace',
            'autocomplete': 'one-time-code',
        }),
    )

    def clean_code(self):
        return ''.join(self.cleaned_data['code'].split())


class PasswordResetSetPasswordForm(InitialPasswordForm):
    pass


class RolForm(MantenedorModelForm):
    permisos = forms.MultipleChoiceField(
        choices=PERMISOS_ROL,
        required=False,
        label='Permisos',
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        help_text='El acceso efectivo de un funcionario combina los permisos de sus roles.',
    )

    class Meta:
        model = Rol
        fields = ('nombre_rol', 'descripcion', 'permisos', 'estado')
        labels = {
            'nombre_rol': 'Nombre',
            'descripcion': 'Descripción',
            'estado': 'Activo',
        }
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'estado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_nombre_rol(self):
        nombre = self.cleaned_data['nombre_rol'].strip()
        duplicates = Rol.objects.filter(nombre_rol__iexact=nombre)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError('Ya existe un rol con ese nombre.')
        return nombre

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['permisos'] = self.instance.permisos

    def save(self, commit=True):
        rol = super().save(commit=False)
        rol.permisos = self.cleaned_data['permisos']
        if commit:
            rol.save()
        return rol


class AreaForm(MantenedorModelForm):
    class Meta:
        model = Area
        fields = ('nombre', 'descripcion', 'estado')
        labels = {
            'nombre': 'Nombre',
            'descripcion': 'Descripción',
            'estado': 'Activo',
        }
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'estado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class CatalogoServicioForm(MantenedorModelForm):
    class Meta:
        model = CatalogoServicio
        fields = ('codigo', 'nombre', 'area')

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()


class AreaCatalogForm(MantenedorModelForm):
    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        area = self.cleaned_data.get('area')
        if not area:
            return nombre
        area_id = area.pk
        duplicates = self._meta.model.objects.filter(
            area_id=area_id,
            nombre__iexact=nombre,
        )
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError('Ya existe una categoría con ese nombre en el área.')
        return nombre


class TipoActividadForm(AreaCatalogForm):
    class Meta:
        model = TipoActividad
        fields = ('area', 'nombre', 'descripcion')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        areas = Area.objects.filter(estado=True)
        if self.instance.pk:
            areas = Area.objects.filter(
                Q(estado=True) | Q(pk=self.instance.area_id)
            )
        self.fields['area'].queryset = areas.order_by('nombre')


class TipoAtencionSocialForm(AreaCatalogForm):
    class Meta:
        model = TipoAtencionSocial
        fields = ('area', 'nombre', 'descripcion')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        areas = Area.objects.filter(estado=True)
        if self.instance.pk:
            areas = Area.objects.filter(
                Q(estado=True) | Q(pk=self.instance.area_id)
            )
        self.fields['area'].queryset = areas.order_by('nombre')


class SubatencionSocialForm(MantenedorModelForm):
    class Meta:
        model = SubatencionSocial
        fields = ('tipo_atencion', 'nombre', 'descripcion')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tipos = TipoAtencionSocial.objects.filter(
            activo=True,
            area__estado=True,
        )
        if self.instance.pk:
            tipos = TipoAtencionSocial.objects.filter(
                Q(activo=True, area__estado=True)
                | Q(pk=self.instance.tipo_atencion_id)
            )
        self.fields['tipo_atencion'].queryset = tipos.select_related(
            'area'
        ).order_by('area__nombre', 'nombre')

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        tipo_atencion = self.cleaned_data.get('tipo_atencion')
        if not tipo_atencion:
            return nombre
        tipo_atencion_id = tipo_atencion.pk
        duplicates = SubatencionSocial.objects.filter(
            tipo_atencion_id=tipo_atencion_id,
            nombre__iexact=nombre,
        )
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError(
                'Ya existe una subatención con ese nombre para el tipo seleccionado.'
            )
        return nombre


class ActividadFotosInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class ActividadFotosField(forms.ImageField):
    widget = ActividadFotosInput

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        from sgr_app.services.evidencias import validar_archivo_evidencia

        for uploaded_file in files:
            validar_archivo_evidencia(uploaded_file)
        cleaned_files = [super().clean(file, initial) for file in files]
        return cleaned_files


class ActividadTerrenoForm(forms.Form):
    funcionario_id = forms.ModelChoiceField(
        queryset=Usuario.objects.none(),
        required=False,
        label='Funcionario responsable',
    )
    fecha_actividad = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Fecha de actividad',
    )
    item_medicion_id = forms.ModelChoiceField(
        queryset=ItemMedicion.objects.none(),
        required=False,
        label='Ítem de medición',
    )
    tipo_actividad_id = forms.ModelChoiceField(
        queryset=TipoActividad.objects.none(),
        required=False,
        label='Tipo de actividad',
    )
    catalogo_servicio_id = forms.ModelChoiceField(
        queryset=CatalogoServicio.objects.none(),
        required=False,
        label='Servicio del catálogo',
    )
    solicitante_id = forms.ModelChoiceField(
        queryset=Solicitante.objects.all(),
        label='Solicitante',
    )
    contacto_telefono = forms.CharField(
        required=True,
        max_length=50,
        label='Teléfono de contacto',
        error_messages={'required': 'El teléfono de contacto es obligatorio.'},
    )
    direccion = forms.CharField(max_length=255, label='Dirección o sector')
    solicitud_problema = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Problema o solicitud detectada',
    )
    accion_ejecutada = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Acción ejecutada',
    )
    latitud = forms.FloatField(required=False, min_value=-90, max_value=90)
    longitud = forms.FloatField(required=False, min_value=-180, max_value=180)
    fotos = ActividadFotosField(required=False, label='Fotografías de respaldo')
    crear_compromiso = forms.BooleanField(required=False, label='Crear compromiso de seguimiento')
    compromiso_fecha = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Fecha de compromiso',
    )
    compromiso_territorio = forms.ChoiceField(
        required=False,
        choices=[('', 'Selecciona un territorio')] + [
            (territorio, territorio) for territorio in ACTIVIDAD_TERRITORIOS
        ],
        label='Territorio',
    )
    compromiso_area_apoyo = forms.ChoiceField(
        required=False,
        choices=[('', 'Selecciona un área')] + [
            (area, area) for area in ACTIVIDAD_AREAS_APOYO
        ],
        label='Área de apoyo',
    )
    compromiso_descripcion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Descripción del compromiso',
    )

    def __init__(
        self,
        *args,
        funcionario_queryset,
        item_queryset,
        tipo_actividad_queryset,
        catalogo_queryset,
        periodo,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields['funcionario_id'].queryset = funcionario_queryset
        self.fields['item_medicion_id'].queryset = item_queryset
        self.fields['tipo_actividad_id'].queryset = tipo_actividad_queryset
        self.fields['tipo_actividad_id'].required = tipo_actividad_queryset.exists()
        self.fields['catalogo_servicio_id'].queryset = catalogo_queryset
        self.periodo = periodo
        for field_name in ('funcionario_id', 'item_medicion_id', 'solicitante_id'):
            self.fields[field_name].widget.attrs['class'] = 'form-select'

    def clean_contacto_telefono(self):
        value = self.cleaned_data['contacto_telefono'].strip()
        try:
            return normalize_chilean_phone(value)
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc

    def clean(self):
        cleaned_data = super().clean()
        actividad_fecha = cleaned_data.get('fecha_actividad')
        item = cleaned_data.get('item_medicion_id')
        funcionario = cleaned_data.get('funcionario_id')

        if actividad_fecha and actividad_fecha > timezone.localdate():
            self.add_error('fecha_actividad', 'La fecha no puede ser posterior a hoy.')

        if (
            item
            and actividad_fecha
            and not item.periodo.fecha_inicio <= actividad_fecha <= item.periodo.fecha_termino
        ):
            self.add_error(
                'fecha_actividad',
                'La fecha de actividad debe pertenecer al período del ítem seleccionado.',
            )

        if funcionario and funcionario.cargo_id and self.fields['item_medicion_id'].queryset.exists() and not item:
            self.add_error('item_medicion_id', 'Selecciona un ítem válido.')

        if (cleaned_data.get('latitud') is None) != (cleaned_data.get('longitud') is None):
            self.add_error('latitud', 'Debes ingresar latitud y longitud conjuntamente.')

        commitment_fields = (
            'compromiso_fecha',
            'compromiso_territorio',
            'compromiso_area_apoyo',
            'compromiso_descripcion',
        )
        wants_commitment = cleaned_data.get('crear_compromiso') or any(
            cleaned_data.get(field_name) for field_name in commitment_fields
        )
        cleaned_data['crear_compromiso'] = wants_commitment
        if wants_commitment:
            fecha_compromiso = cleaned_data.get('compromiso_fecha')
            if not self.periodo:
                self.add_error('compromiso_fecha', 'No existe un período activo para el compromiso.')
            elif fecha_compromiso and actividad_fecha:
                if not (self.periodo.fecha_inicio <= fecha_compromiso <= self.periodo.fecha_termino):
                    self.add_error('compromiso_fecha', 'La fecha debe pertenecer al período activo.')
                if fecha_compromiso < actividad_fecha:
                    self.add_error('compromiso_fecha', 'No puede ser anterior a la actividad.')
            for name in commitment_fields:
                if not cleaned_data.get(name):
                    self.add_error(name, 'Este campo es obligatorio al crear un compromiso.')

        return cleaned_data


class ValidarEvidenciaForm(forms.Form):
    decision = forms.ChoiceField(choices=(
        (Evidencia.Estado.APROBADA, 'Aprobar'),
        (Evidencia.Estado.RECHAZADA, 'Rechazar'),
        (Evidencia.Estado.CORRECCION, 'Solicitar corrección'),
    ))
    observacion = forms.CharField(required=False, widget=forms.Textarea)

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get('decision') in (
                Evidencia.Estado.RECHAZADA,
                Evidencia.Estado.CORRECCION,
            )
            and not cleaned_data.get('observacion', '').strip()
        ):
            self.add_error(
                'observacion',
                'Indica el motivo del rechazo o la corrección solicitada.',
            )
        elif 'observacion' in cleaned_data:
            cleaned_data['observacion'] = cleaned_data['observacion'].strip()
        return cleaned_data
