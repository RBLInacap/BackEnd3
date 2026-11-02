from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from sgr_app.models import (
    Area,
    AjusteIncentivo,
    Cargo,
    CasoSocial,
    CatalogoServicio,
    CompromisoAgenda,
    Departamento,
    Delegacion,
    Evidencia,
    ItemMedicion,
    PeriodoMedicion,
    Rol,
    Solicitante,
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
        elif user.es_delegado:
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

        if delegation and responsible and responsible.delegacion_id != delegation.pk:
            self.add_error(
                'responsable',
                'El responsable debe pertenecer a la delegación seleccionada.',
            )
        if self.user.es_delegado and delegation and delegation.pk != self.user.delegacion_id:
            self.add_error('delegacion', 'Solo puedes gestionar compromisos de tu delegación.')
        if self.user.es_administrativo and responsible and responsible.pk != self.user.pk:
            self.add_error('responsable', 'Solo puedes asignarte compromisos a ti mismo.')
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
        actividad = self.instance.actividad if self.instance.pk else None
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
    class Meta:
        model = CasoSocial
        fields = ('codigo_caso', 'persona_anonimizada', 'delegacion', 'fecha_apertura')
        widgets = {
            'fecha_apertura': forms.DateInput(attrs={'type': 'date'}),
            'persona_anonimizada': forms.TextInput(attrs={
                'autocomplete': 'off',
                'placeholder': 'Código o identificación seudonimizada',
            }),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['delegacion'].queryset = Delegacion.objects.filter(activo=True)
        if user.es_delegado:
            self.fields['delegacion'].queryset = self.fields['delegacion'].queryset.filter(
                pk=user.delegacion_id,
            )
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )


class AtencionSocialForm(forms.Form):
    tipo_atencion = forms.CharField(max_length=100, label='Tipo de atención')
    detalle = forms.CharField(
        label='Detalle',
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    resultado = forms.CharField(max_length=150, label='Resultado')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


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
            'telefono': forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel', 'maxlength': 30}),
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
    class Meta:
        model = Delegacion
        fields = ('nombre', 'codigo', 'direccion', 'telefono', 'email', 'ambito')
        widgets = {
            'direccion': forms.TextInput(),
            'telefono': forms.TextInput(),
            'email': forms.EmailInput(),
        }


class CargoForm(MantenedorModelForm):
    class Meta:
        model = Cargo
        fields = ('nombre', 'descripcion', 'departamento')
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        departamentos = Departamento.objects.filter(delegacion__activo=True).order_by(
            'delegacion__nombre',
            'nombre_departamento',
        )
        if self.instance.pk and self.instance.departamento_id:
            departamentos = (Departamento.objects.filter(
                pk=self.instance.departamento_id,
            ) | departamentos).distinct()
        self.fields['departamento'].queryset = departamentos
        self.fields['departamento'].required = False
        self.fields['departamento'].widget.attrs['class'] = 'form-select'


class MantenedorItemForm(MantenedorModelForm):
    class Meta:
        model = ItemMedicion
        fields = (
            'cargo',
            'periodo',
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
            'ponderador': 'Ponderador (%)',
            'unidad_medida': 'Unidad de medida',
            'activo': 'Activo',
        }
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'cargo': forms.Select(attrs={'class': 'form-select'}),
            'periodo': forms.Select(attrs={'class': 'form-select'}),
            'tipo_unidad': forms.Select(attrs={'class': 'form-select'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cargos = Cargo.objects.filter(activo=True).order_by('nombre')
        periodos = PeriodoMedicion.objects.filter(estado=True).order_by('-fecha_inicio')

        if self.instance.pk:
            if self.instance.cargo_id:
                cargos = (Cargo.objects.filter(pk=self.instance.cargo_id) | cargos).distinct()
            if self.instance.periodo_id:
                periodos = (
                    PeriodoMedicion.objects.filter(pk=self.instance.periodo_id) | periodos
                ).distinct()

        self.fields['cargo'].queryset = cargos
        self.fields['periodo'].queryset = periodos

    def clean(self):
        cleaned_data = super().clean()
        periodo = cleaned_data.get('periodo')
        if periodo and periodo.cerrado:
            self.add_error(
                'periodo',
                'No se pueden crear ni modificar ítems de un período cerrado.',
            )
        return cleaned_data


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
            'umbral_verde',
            'umbral_ambar',
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
            'umbral_verde': 'Umbral de cumplimiento verde (%)',
            'umbral_ambar': 'Umbral de cumplimiento ámbar (%)',
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
            'umbral_verde': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'umbral_ambar': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
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

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and self.instance.cerrado:
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
    rol = forms.ChoiceField(choices=Usuario.Rol.choices, initial=Usuario.Rol.ADMINISTRATIVO)
    es_delegado = forms.BooleanField(
        label='Perfil Delegado',
        required=False,
        help_text='Limita el acceso del Supervisor a los datos de su delegación.',
    )
    area_entidad = forms.ModelChoiceField(
        queryset=Area.objects.filter(estado=True).order_by('nombre'),
        required=False,
        label='Área',
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
    estado = forms.BooleanField(label='Activo', required=False)

    class Meta:
        model = Usuario
        fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ('area_entidad', 'cargo', 'delegacion'):
            self.fields[field_name].widget.attrs['class'] = 'form-select'
        self.fields['estado'].widget.attrs['class'] = 'form-check-input'

        if self.instance.pk:
            run = self.instance.rut_anonimizado or ''
            apellidos = (self.instance.last_name or '').split(maxsplit=1)
            self.initial.update({
                'run': run,
                'nombre': self.instance.first_name,
                'apellidoPaterno': apellidos[0] if apellidos else '',
                'apellidoMaterno': apellidos[1] if len(apellidos) > 1 else '',
                'correo': self.instance.email,
                'estado': self.instance.is_active,
                'es_delegado': self.instance.es_delegado,
            })

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
        es_delegado = cleaned_data.get('es_delegado', False)
        if es_delegado and rol != Usuario.Rol.SUPERVISOR:
            self.add_error('rol', 'El perfil Delegado requiere el rol Supervisor.')
        if es_delegado and not delegacion:
            self.add_error(
                'delegacion',
                'El perfil Delegado requiere una delegación asignada.',
            )
        return cleaned_data

    def save(self, commit=True):
        funcionario = self.instance
        run = self.cleaned_data['run']
        funcionario.username = funcionario.username or run
        funcionario.rut_anonimizado = run
        funcionario.first_name = self.cleaned_data['nombre'].strip()
        funcionario.last_name = ' '.join(filter(None, (
            self.cleaned_data['apellidoPaterno'].strip(),
            self.cleaned_data['apellidoMaterno'].strip(),
        )))
        funcionario.email = self.cleaned_data['correo']
        funcionario.rol = self.cleaned_data['rol']
        funcionario.es_delegado = self.cleaned_data['es_delegado']
        funcionario.area_entidad = self.cleaned_data['area_entidad']
        funcionario.cargo = self.cleaned_data['cargo']
        funcionario.delegacion = self.cleaned_data['delegacion']
        funcionario.is_active = self.cleaned_data['estado']
        if commit:
            funcionario.save()
        return funcionario


class RolForm(MantenedorModelForm):
    class Meta:
        model = Rol
        fields = ('nombre_rol', 'descripcion', 'estado')
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
    catalogo_servicio_id = forms.ModelChoiceField(
        queryset=CatalogoServicio.objects.none(),
        required=False,
        label='Servicio del catálogo',
    )
    solicitante_id = forms.ModelChoiceField(
        queryset=Solicitante.objects.all(),
        label='Solicitante',
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
        catalogo_queryset,
        periodo,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields['funcionario_id'].queryset = funcionario_queryset
        self.fields['item_medicion_id'].queryset = item_queryset
        self.fields['catalogo_servicio_id'].queryset = catalogo_queryset
        self.periodo = periodo
        for field_name in ('funcionario_id', 'item_medicion_id', 'solicitante_id'):
            self.fields[field_name].widget.attrs['class'] = 'form-select'

    def clean(self):
        cleaned_data = super().clean()
        actividad_fecha = cleaned_data.get('fecha_actividad')
        item = cleaned_data.get('item_medicion_id')
        funcionario = cleaned_data.get('funcionario_id')

        if actividad_fecha and actividad_fecha > timezone.localdate():
            self.add_error('fecha_actividad', 'La fecha no puede ser posterior a hoy.')

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
