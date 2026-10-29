from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
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

    class Meta:
        model = Usuario
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email', 'rol', 'es_delegado',
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

    class Meta:
        model = Evidencia
        fields = [
            'id', 'actividad', 'codigo_verificador', 'archivo_foto', 'archivo_foto_2', 'archivo_foto_3',
            'url_foto_simulada', 'url_foto_simulada_2', 'url_foto_simulada_3',
            'hash_sha256', 'estado', 'verificador', 'verificador_nombre',
            'fecha_validacion', 'observacion', 'fecha_subida'
        ]
        read_only_fields = ['id', 'codigo_verificador', 'hash_sha256', 'fecha_subida']


class CompromisoAgendaSerializer(serializers.ModelSerializer):
    delegacion_nombre = serializers.ReadOnlyField(source='delegacion.nombre')
    delegacion_codigo = serializers.ReadOnlyField(source='delegacion.codigo')
    responsable_nombre = serializers.ReadOnlyField(source='responsable.get_full_name')

    class Meta:
        model = CompromisoAgenda
        fields = '__all__'

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
    contacto_telefono = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    funcionario_nombre = serializers.ReadOnlyField(source='funcionario.get_full_name')
    delegacion_nombre = serializers.ReadOnlyField(source='delegacion.nombre')
    delegacion_codigo = serializers.ReadOnlyField(source='delegacion.codigo')
    item_nombre = serializers.ReadOnlyField(source='item_medicion.nombre_item')
    evidencia = EvidenciaSerializer(read_only=True)

    class Meta:
        model = Actividad
        fields = [
            'id', 'fecha_registro', 'fecha_actividad', 'funcionario', 'funcionario_nombre',
            'delegacion', 'delegacion_nombre', 'delegacion_codigo', 'item_medicion', 'item_nombre',
            'catalogo_servicio', 'catalogo_servicio_nombre', 'catalogo_servicio_area',
            'actividad_solicitud', 'accion_ejecutada', 'direccion',
            'contacto_nombre', 'contacto_telefono', 'latitud', 'longitud',
            'compromiso_agenda', 'evidencia', 'creado_en', 'actualizado_en'
        ]
        read_only_fields = [
            'id',
            'fecha_registro',
            'catalogo_servicio_nombre',
            'catalogo_servicio_area',
            'creado_en',
            'actualizado_en',
        ]

    def validate_contacto_telefono(self, value):
        if not value:
            return ''
        try:
            return normalize_chilean_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc


class AtencionSocialSerializer(serializers.ModelSerializer):
    funcionario_nombre = serializers.ReadOnlyField(source='funcionario.get_full_name')

    class Meta:
        model = AtencionSocial
        fields = '__all__'


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
