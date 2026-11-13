from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from sgr_app.services.evidencias import metadatos_archivo_evidencia, validar_archivo_evidencia
from sgr_app.models import (
    Usuario, Delegacion, Cargo, PeriodoMedicion, CatalogoServicio,
    ItemMedicion, CompromisoAgenda, Actividad, Evidencia,
    CasoSocial, AtencionSocial, RegistroAuditoria
)
from sgr_app.validators import (
    normalize_chilean_phone,
    normalize_chilean_rut,
    validate_chilean_rut,
)


class DelegacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Delegacion
        fields = '__all__'


class CargoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cargo
        fields = '__all__'


class UsuarioSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    telefono = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    rut_anonimizado = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    cargo_nombre = serializers.ReadOnlyField(source='cargo.nombre')
    delegacion_nombre = serializers.ReadOnlyField(source='delegacion.nombre')
    delegacion_codigo = serializers.ReadOnlyField(source='delegacion.codigo')
    roles = serializers.StringRelatedField(many=True, read_only=True)

    class Meta:
        model = Usuario
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email', 'rol', 'roles', 'es_delegado',
            'cargo', 'cargo_nombre', 'delegacion', 'delegacion_nombre', 'delegacion_codigo',
            'telefono', 'rut_anonimizado', 'requires_2fa', 'is_active'
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        role = attrs.get('rol', getattr(self.instance, 'rol', None))
        is_delegate = attrs.get(
            'es_delegado',
            getattr(self.instance, 'es_delegado', False),
        )
        delegation = attrs.get(
            'delegacion',
            getattr(self.instance, 'delegacion', None),
        )
        if is_delegate and role != Usuario.Rol.SUPERVISOR:
            raise serializers.ValidationError({
                'es_delegado': 'El perfil Delegado requiere el rol Supervisor.',
            })
        if is_delegate and not delegation:
            raise serializers.ValidationError({
                'delegacion': 'El perfil Delegado requiere una delegación asignada.',
            })
        return attrs

    def validate_rut_anonimizado(self, value):
        if not value:
            return value
        try:
            validate_chilean_rut(value)
            return normalize_chilean_rut(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def validate_telefono(self, value):
        if not value:
            return ''
        try:
            return normalize_chilean_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def validate_email(self, value):
        return value.strip()


class PeriodoMedicionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PeriodoMedicion
        fields = '__all__'


class CatalogoServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogoServicio
        fields = '__all__'


class ItemMedicionSerializer(serializers.ModelSerializer):
    cargo_nombre = serializers.ReadOnlyField(source='cargo.nombre')
    periodo_nombre = serializers.ReadOnlyField(source='periodo.nombre')

    class Meta:
        model = ItemMedicion
        fields = '__all__'


class EvidenciaSerializer(serializers.ModelSerializer):
    verificador_nombre = serializers.ReadOnlyField(source='verificador.get_full_name')
    subido_por_nombre = serializers.ReadOnlyField(source='subido_por.get_full_name')

    class Meta:
        model = Evidencia
        fields = [
            'id', 'actividad', 'codigo_verificador', 'archivo_foto', 'archivo_foto_2', 'archivo_foto_3',
            'url_foto_simulada', 'url_foto_simulada_2', 'url_foto_simulada_3',
            'hash_sha256', 'estado', 'verificador', 'verificador_nombre',
            'fecha_validacion', 'observacion', 'fecha_subida', 'metadatos',
            'subido_por', 'subido_por_nombre', 'archivos_eliminados_en',
        ]
        read_only_fields = [
            'id', 'codigo_verificador', 'hash_sha256', 'fecha_subida',
            'metadatos', 'subido_por', 'subido_por_nombre', 'archivos_eliminados_en',
        ]

    def validate(self, attrs):
        actividad = attrs.get('actividad')
        if self.instance is None and actividad and actividad.anulada:
            raise serializers.ValidationError({
                'actividad': 'No se puede asociar evidencia a una actividad anulada.'
            })
        if self.instance is None and actividad and Evidencia.objects.filter(
            actividad=actividad,
            estado=Evidencia.Estado.APROBADA,
        ).exists():
            raise serializers.ValidationError({
                'actividad': 'La actividad está cerrada porque ya tiene una evidencia aprobada.'
            })
        for campo in ('archivo_foto', 'archivo_foto_2', 'archivo_foto_3'):
            archivo = attrs.get(campo)
            if archivo:
                try:
                    validar_archivo_evidencia(archivo)
                except DjangoValidationError as exc:
                    raise serializers.ValidationError({campo: exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        subido_por = request.user if request else None
        archivos = [
            validated_data[campo]
            for campo in ('archivo_foto', 'archivo_foto_2', 'archivo_foto_3')
            if validated_data.get(campo)
        ]
        if archivos:
            validated_data['metadatos'] = {
                'archivos': [
                    metadatos_archivo_evidencia(archivo, subido_por)
                    for archivo in archivos
                ]
            }
            validated_data['subido_por'] = subido_por
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        subido_por = request.user if request else None
        archivos = [
            validated_data[campo]
            for campo in ('archivo_foto', 'archivo_foto_2', 'archivo_foto_3')
            if validated_data.get(campo)
        ]
        if archivos:
            instance.metadatos = {
                'archivos': [
                    metadatos_archivo_evidencia(archivo, subido_por)
                    for archivo in archivos
                ]
            }
            instance.subido_por = subido_por
        return super().update(instance, validated_data)


class CompromisoAgendaSerializer(serializers.ModelSerializer):
    territorio = serializers.CharField(required=True, allow_blank=False)
    area_apoyo = serializers.CharField(required=True, allow_blank=False)
    fecha_compromiso = serializers.DateField(required=False)
    fecha_comprometida = serializers.DateField(required=False)
    delegacion_nombre = serializers.ReadOnlyField(source='delegacion.nombre')
    delegacion_codigo = serializers.ReadOnlyField(source='delegacion.codigo')
    responsable_nombre = serializers.ReadOnlyField(source='responsable.get_full_name')

    class Meta:
        model = CompromisoAgenda
        fields = '__all__'

    def validate(self, attrs):
        attrs = super().validate(attrs)
        fecha = attrs.get(
            'fecha_comprometida',
            attrs.get(
                'fecha_compromiso',
                getattr(
                    self.instance,
                    'fecha_comprometida',
                    getattr(self.instance, 'fecha_compromiso', None),
                ),
            ),
        )
        if not fecha:
            raise serializers.ValidationError({
                'fecha_comprometida': 'La fecha comprometida es obligatoria.'
            })
        if fecha < timezone.localdate():
            raise serializers.ValidationError({
                'fecha_comprometida': 'La fecha comprometida no puede ser anterior a hoy.'
            })
        delegacion = attrs.get(
            'delegacion',
            getattr(self.instance, 'delegacion', None),
        )
        responsable = attrs.get(
            'responsable',
            getattr(self.instance, 'responsable', None),
        )
        if delegacion and responsable and responsable.delegacion_id != delegacion.pk:
            raise serializers.ValidationError({
                'responsable': 'El responsable debe pertenecer a la delegación seleccionada.'
            })
        actividad = attrs.get(
            'actividad',
            getattr(self.instance, 'actividad', None),
        )
        if actividad and fecha < actividad.fecha_actividad:
            raise serializers.ValidationError({
                'fecha_comprometida': (
                    'La fecha comprometida no puede ser anterior a la fecha de la actividad.'
                )
            })
        if actividad:
            if actividad.anulada:
                raise serializers.ValidationError({
                    'actividad': 'No se puede asociar un compromiso a una actividad anulada.'
                })
            if delegacion and actividad.delegacion_id != delegacion.pk:
                raise serializers.ValidationError({
                    'actividad': 'La actividad debe pertenecer a la delegación seleccionada.'
                })
            actividades_vinculadas = CompromisoAgenda.objects.filter(
                actividad=actividad,
            )
            if self.instance:
                actividades_vinculadas = actividades_vinculadas.exclude(
                    pk=self.instance.pk,
                )
            if actividades_vinculadas.exists():
                raise serializers.ValidationError({
                    'actividad': 'La actividad ya está vinculada a otro compromiso.'
                })
        return attrs

    def create(self, validated_data):
        fecha = validated_data.get(
            'fecha_comprometida',
            validated_data.get('fecha_compromiso'),
        )
        validated_data['fecha_compromiso'] = fecha
        validated_data['fecha_comprometida'] = fecha
        return super().create(validated_data)

    def update(self, instance, validated_data):
        fecha = validated_data.get(
            'fecha_comprometida',
            validated_data.get('fecha_compromiso'),
        )
        if fecha:
            validated_data['fecha_compromiso'] = fecha
            validated_data['fecha_comprometida'] = fecha
        return super().update(instance, validated_data)

    def validate_estado(self, value):
        estados_editables = [
            CompromisoAgenda.Estado.INGRESADO,
            CompromisoAgenda.Estado.PENDIENTE,
            CompromisoAgenda.Estado.EN_PROCESO,
            CompromisoAgenda.Estado.REPROGRAMADO,
        ]
        if value not in estados_editables and (self.instance is None or value != self.instance.estado):
            raise serializers.ValidationError(
                'El estado completado se determina mediante la validación de evidencias.'
            )
        return value


class ActividadSerializer(serializers.ModelSerializer):
    contacto_telefono = serializers.CharField(required=True, allow_blank=False, allow_null=False)
    codigo_registro = serializers.ReadOnlyField()
    funcionario_nombre = serializers.ReadOnlyField(source='funcionario.get_full_name')
    delegacion_nombre = serializers.ReadOnlyField(source='delegacion.nombre')
    delegacion_codigo = serializers.ReadOnlyField(source='delegacion.codigo')
    item_nombre = serializers.ReadOnlyField(source='item_medicion.nombre_item')
    evidencia = EvidenciaSerializer(read_only=True)

    class Meta:
        model = Actividad
        fields = [
            'id', 'codigo_registro', 'fecha_registro', 'fecha_actividad',
            'anulada', 'anulada_en', 'anulada_por', 'motivo_anulacion',
            'funcionario', 'funcionario_nombre',
            'delegacion', 'delegacion_nombre', 'delegacion_codigo', 'item_medicion', 'item_nombre',
            'catalogo_servicio', 'catalogo_servicio_nombre', 'catalogo_servicio_area',
            'tipo_actividad', 'tipo_actividad_nombre', 'tipo_actividad_area',
            'actividad_solicitud', 'accion_ejecutada', 'direccion',
            'contacto_nombre', 'contacto_telefono', 'latitud', 'longitud',
            'compromiso_agenda', 'evidencia', 'creado_en', 'actualizado_en'
        ]
        read_only_fields = [
            'id',
            'codigo_registro',
            'fecha_registro',
            'anulada',
            'anulada_en',
            'anulada_por',
            'motivo_anulacion',
            'catalogo_servicio_nombre',
            'catalogo_servicio_area',
            'tipo_actividad_nombre',
            'tipo_actividad_area',
            'creado_en',
            'actualizado_en',
        ]

    def validate_contacto_telefono(self, value):
        if not value:
            raise serializers.ValidationError('El teléfono de contacto es obligatorio.')
        try:
            return normalize_chilean_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def validate(self, attrs):
        errors = {}
        fecha_actividad = attrs.get(
            'fecha_actividad',
            self.instance.fecha_actividad if self.instance else None,
        )
        item_medicion = attrs.get(
            'item_medicion',
            self.instance.item_medicion if self.instance else None,
        )
        tipo_actividad = attrs.get(
            'tipo_actividad',
            self.instance.tipo_actividad if self.instance else None,
        )
        funcionario = attrs.get(
            'funcionario',
            self.instance.funcionario if self.instance else None,
        )
        tipo_actividad_cambiado = (
            self.instance is None
            or attrs.get('tipo_actividad') not in (None, tipo_actividad)
        )
        item_context_changed = (
            self.instance is None
            or 'item_medicion' in attrs
            or 'fecha_actividad' in attrs
            or 'funcionario' in attrs
        )

        if fecha_actividad and fecha_actividad > timezone.localdate():
            errors['fecha_actividad'] = 'La fecha de actividad no puede ser futura.'

        if item_context_changed:
            items_vigentes = ItemMedicion.objects.none()
            if funcionario and funcionario.cargo_id and fecha_actividad:
                items_vigentes = ItemMedicion.objects.filter(
                    cargo_id=funcionario.cargo_id,
                    activo=True,
                    periodo__fecha_inicio__lte=fecha_actividad,
                    periodo__fecha_termino__gte=fecha_actividad,
                )

            if items_vigentes.exists() and not item_medicion:
                errors['item_medicion'] = (
                    'Selecciona un ítem vigente para el cargo y período del funcionario.'
                )
            elif item_medicion and not items_vigentes.filter(pk=item_medicion.pk).exists():
                errors['item_medicion'] = (
                    'El ítem debe estar activo y corresponder al cargo y período de la actividad.'
                )

        if tipo_actividad and tipo_actividad_cambiado:
            if not tipo_actividad.activo or not tipo_actividad.area.estado:
                errors['tipo_actividad'] = 'El tipo de actividad seleccionado está inactivo.'
            if (
                funcionario
                and funcionario.area_entidad_id
                and tipo_actividad.area_id != funcionario.area_entidad_id
            ):
                errors['tipo_actividad'] = 'El tipo de actividad no pertenece al área del funcionario.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class AtencionSocialSerializer(serializers.ModelSerializer):
    funcionario = serializers.PrimaryKeyRelatedField(read_only=True)
    funcionario_nombre = serializers.ReadOnlyField(source='funcionario.get_full_name')
    tipo_atencion_area = serializers.ReadOnlyField()
    subatencion = serializers.ReadOnlyField()
    tipo_atencion = serializers.CharField(
        required=False,
        max_length=100,
    )

    class Meta:
        model = AtencionSocial
        fields = '__all__'

    def validate(self, attrs):
        etapa = attrs.get(
            'etapa',
            self.instance.etapa if self.instance else None,
        )
        fecha_fields = {
            1: ('fecha_visita_programada', 'La fecha programada de visita'),
            2: ('fecha_visita', 'La fecha de visita realizada'),
            3: ('fecha_entrega_beneficio', 'La fecha de entrega del beneficio'),
        }
        if etapa not in fecha_fields:
            raise serializers.ValidationError({'etapa': 'Selecciona una etapa válida.'})
        fecha_field, label = fecha_fields[etapa]
        fecha = attrs.get(
            fecha_field,
            getattr(self.instance, fecha_field, None)
            if self.instance
            else None,
        )
        if not fecha:
            raise serializers.ValidationError({
                fecha_field: f'{label} es obligatoria para esta gestión.'
            })
        today = timezone.localdate()
        if etapa == 1 and fecha < today:
            raise serializers.ValidationError({
                fecha_field: 'La fecha programada de visita no puede ser anterior a hoy.'
            })
        if etapa in (2, 3) and fecha > today:
            raise serializers.ValidationError({
                fecha_field: 'La fecha de una gestión realizada no puede ser futura.'
            })
        tipo_atencion = attrs.get(
            'tipo_atencion_catalogo',
            self.instance.tipo_atencion_catalogo if self.instance else None,
        )
        funcionario = attrs.get(
            'funcionario',
            self.instance.funcionario if self.instance else None,
        )
        subatencion = attrs.get(
            'subatencion_catalogo',
            self.instance.subatencion_catalogo if self.instance else None,
        )
        tipo_atencion_cambiado = (
            self.instance is None
            or attrs.get('tipo_atencion_catalogo') not in (None, tipo_atencion)
        )
        if tipo_atencion and tipo_atencion_cambiado and (
            not tipo_atencion.activo or not tipo_atencion.area.estado
        ):
            raise serializers.ValidationError({
                'tipo_atencion_catalogo': 'El tipo de atención seleccionado está inactivo.'
            })
        if (
            tipo_atencion
            and tipo_atencion_cambiado
            and funcionario
            and funcionario.area_entidad_id
            and tipo_atencion.area_id != funcionario.area_entidad_id
        ):
            raise serializers.ValidationError({
                'tipo_atencion_catalogo': (
                    'El tipo de atención no pertenece al área del funcionario.'
                )
            })
        subatencion_cambiada = (
            self.instance is None
            or attrs.get('subatencion_catalogo') not in (None, subatencion)
        )
        if subatencion and subatencion_cambiada:
            if not subatencion.activo:
                raise serializers.ValidationError({
                    'subatencion_catalogo': 'La subatención seleccionada está inactiva.'
                })
            if not tipo_atencion or subatencion.tipo_atencion_id != tipo_atencion.pk:
                raise serializers.ValidationError({
                    'subatencion_catalogo': (
                        'La subatención debe pertenecer al tipo de atención seleccionado.'
                    )
                })
        if not (attrs.get('tipo_atencion') or tipo_atencion):
            raise serializers.ValidationError({
                'tipo_atencion': 'El tipo de atención es obligatorio.'
            })
        caso = attrs.get('caso', self.instance.caso if self.instance else None)
        if self.instance is None and caso:
            if caso.cerrado:
                raise serializers.ValidationError({
                    'caso': 'No se pueden registrar gestiones en un caso cerrado.'
                })
            siguiente_etapa = (
                caso.atenciones.order_by('-etapa')
                .values_list('etapa', flat=True)
                .first()
                or 0
            ) + 1
            if siguiente_etapa > 3:
                raise serializers.ValidationError({
                    'caso': 'El caso ya tiene el máximo de tres gestiones.'
                })
            if etapa != siguiente_etapa:
                raise serializers.ValidationError({
                    'etapa': f'La siguiente gestión disponible es la etapa {siguiente_etapa}.'
                })
        return attrs

    def create(self, validated_data):
        caso = validated_data.pop('caso')
        funcionario = validated_data.pop('funcionario', None)
        if funcionario is None:
            request = self.context.get('request')
            funcionario = getattr(request, 'user', None)
        if funcionario is None or not funcionario.is_authenticated:
            raise serializers.ValidationError({
                'funcionario': 'El funcionario debe provenir de un usuario autenticado.'
            })
        etapa = validated_data.pop('etapa')
        fecha_por_etapa = {
            1: 'fecha_visita_programada',
            2: 'fecha_visita',
            3: 'fecha_entrega_beneficio',
        }
        fecha = validated_data.pop(fecha_por_etapa[etapa])
        for field in fecha_por_etapa.values():
            validated_data.pop(field, None)
        tipo_atencion_catalogo = validated_data.pop(
            'tipo_atencion_catalogo',
            None,
        )
        subatencion_catalogo = validated_data.pop(
            'subatencion_catalogo',
            None,
        )
        tipo_atencion = validated_data.pop('tipo_atencion', '')
        return caso.registrar_atencion(
            funcionario=funcionario,
            tipo_atencion=tipo_atencion,
            detalle=validated_data.pop('detalle'),
            resultado=validated_data.pop('resultado'),
            fecha_gestion=fecha,
            tipo_atencion_catalogo=tipo_atencion_catalogo,
            subatencion_catalogo=subatencion_catalogo,
        )


class CasoSocialSerializer(serializers.ModelSerializer):
    delegacion_nombre = serializers.ReadOnlyField(source='delegacion.nombre')
    atenciones = AtencionSocialSerializer(many=True, read_only=True)

    class Meta:
        model = CasoSocial
        fields = '__all__'

    def validate(self, attrs):
        for field_name, label in (
            ('codigo_caso', 'El código del caso'),
            ('persona_anonimizada', 'La identificación anonimizada'),
        ):
            value = attrs.get(field_name, getattr(self.instance, field_name, '') if self.instance else '')
            if not value or not value.strip():
                raise serializers.ValidationError({field_name: f'{label} es obligatorio.'})
            attrs[field_name] = value.strip()
        return attrs


class RegistroAuditoriaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.ReadOnlyField(source='usuario.get_full_name')

    class Meta:
        model = RegistroAuditoria
        fields = '__all__'
