import json
from datetime import date
from django.utils import timezone
from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied as DRFPermissionDenied,
    ValidationError as DRFValidationError,
)

from sgr_app.permissions import AuditAccessPermission
from sgr_app.models import (
    Usuario, Delegacion, Cargo, PeriodoMedicion, CatalogoServicio,
    ItemMedicion, CompromisoAgenda, Actividad, Evidencia,
    CasoSocial, AtencionSocial, RegistroAuditoria
)
from sgr_app.web_views import es_codigo_2fa_valido
from sgr_app.serializers import (
    UsuarioSerializer, DelegacionSerializer, CargoSerializer,
    PeriodoMedicionSerializer, CatalogoServicioSerializer,
    ItemMedicionSerializer, CompromisoAgendaSerializer,
    ActividadSerializer, EvidenciaSerializer,
    CasoSocialSerializer, AtencionSocialSerializer,
    RegistroAuditoriaSerializer
)
from sgr_app.services.calculos import (
    calcular_metricas_funcionario,
    calcular_resumen_delegacion,
    calcular_resumen_comunal,
    recalcular_indicadores_actividad,
)
from sgr_app.services.evidencias import metadatos_archivo_evidencia, validar_archivo_evidencia


def registrar_evento_auditoria(request, usuario, accion, entidad, identificador, val_ant=None, val_nuevo=None):
    try:
        ip = request.META.get('REMOTE_ADDR') if request else '127.0.0.1'
        RegistroAuditoria.objects.create(
            usuario=usuario,
            accion=accion,
            entidad=entidad,
            identificador_registro=str(identificador),
            valor_anterior=val_ant,
            valor_nuevo=val_nuevo,
            ip_origen=ip
        )
    except Exception as e:
        print(f"Error registrando auditoría: {e}")


class AuthLoginView(APIView):
    """
    Login seguro con autenticación de credenciales y preparación para 2FA.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            return Response(
                {'error': 'Debe proporcionar usuario y contraseña.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = authenticate(username=username, password=password)
        if not user:
            return Response(
                {'error': 'Credenciales inválidas o usuario inactivo.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Si el usuario tiene 2FA activado (módulo de Rodrigo)
        if user.requires_2fa:
            request.session['pending_2fa_user_id'] = user.pk
            request.session.set_expiry(300)
            return Response({
                'requires_2fa': True,
                'user_id': user.id,
                'username': user.username,
                'message': 'Autenticación en dos pasos requerida.'
            }, status=status.HTTP_200_OK)

        login(request._request, user)
        registrar_evento_auditoria(request, user, 'INICIO_SESION', 'Usuario', user.id, None, {'username': user.username})

        serializer = UsuarioSerializer(user)
        return Response({
            'requires_2fa': False,
            'user': serializer.data,
            'authenticated': True
        }, status=status.HTTP_200_OK)


class Verificar2FAView(APIView):
    """
    Endpoint preparado para que Rodrigo valide el código TOTP / 2FA.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        user_id = request.session.get('pending_2fa_user_id')
        code = request.data.get('code')
        if not user_id:
            return Response({'error': 'Debe iniciar sesión con usuario y contraseña antes de verificar 2FA.'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            user = Usuario.objects.get(id=user_id, is_active=True, requires_2fa=True)
        except Usuario.DoesNotExist:
            return Response({'error': 'Usuario no encontrado'}, status=status.HTTP_404_NOT_FOUND)

        if es_codigo_2fa_valido(user, code):
            request.session.pop('pending_2fa_user_id', None)
            login(request._request, user)
            registrar_evento_auditoria(request, user, 'VERIFICACION_2FA', 'Usuario', user.id)
            serializer = UsuarioSerializer(user)
            return Response({
                'success': True,
                'user': serializer.data,
                'authenticated': True
            })
        else:
            return Response({'error': 'Código 2FA incorrecto o inválido'}, status=status.HTTP_400_BAD_REQUEST)


class DelegacionViewSet(viewsets.ModelViewSet):
    queryset = Delegacion.objects.all()
    serializer_class = DelegacionSerializer


class CargoViewSet(viewsets.ModelViewSet):
    queryset = Cargo.objects.all()
    serializer_class = CargoSerializer


class UsuarioViewSet(viewsets.ModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = UsuarioSerializer

    def get_queryset(self):
        qs = Usuario.objects.all()
        if self.request.user.es_administrativo:
            qs = qs.filter(pk=self.request.user.pk)
        elif self.request.user.esta_limitado_a_delegacion:
            qs = qs.filter(delegacion_id=self.request.user.delegacion_id)
        delegacion_id = self.request.query_params.get('delegacion')
        rol = self.request.query_params.get('rol')
        if delegacion_id:
            qs = qs.filter(delegacion_id=delegacion_id)
        if rol:
            qs = qs.filter(rol=rol)
        return qs


class PeriodoMedicionViewSet(viewsets.ModelViewSet):
    queryset = PeriodoMedicion.objects.all()
    serializer_class = PeriodoMedicionSerializer


class CatalogoServicioViewSet(viewsets.ModelViewSet):
    queryset = CatalogoServicio.objects.filter(activo=True)
    serializer_class = CatalogoServicioSerializer


class ItemMedicionViewSet(viewsets.ModelViewSet):
    queryset = ItemMedicion.objects.all()
    serializer_class = ItemMedicionSerializer

    def get_queryset(self):
        qs = ItemMedicion.objects.all()
        cargo_id = self.request.query_params.get('cargo')
        periodo_id = self.request.query_params.get('periodo')
        if cargo_id:
            qs = qs.filter(cargo_id=cargo_id)
        if periodo_id:
            qs = qs.filter(periodo_id=periodo_id)
        return qs


class CompromisoAgendaViewSet(viewsets.ModelViewSet):
    """
    Tubo de Trabajo / Agenda Colectiva.
    Permite transiciones controladas de estado y auditoría.
    """
    queryset = CompromisoAgenda.objects.all()
    serializer_class = CompromisoAgendaSerializer

    def get_queryset(self):
        qs = CompromisoAgenda.objects.all()
        if self.request.user.esta_limitado_a_delegacion:
            qs = qs.filter(delegacion_id=self.request.user.delegacion_id)
        elif self.request.user.es_administrativo:
            from django.db.models import Q
            qs = qs.filter(Q(responsable=self.request.user) | Q(creado_por=self.request.user))
        delegacion_id = self.request.query_params.get('delegacion')
        responsable_id = self.request.query_params.get('responsable')
        estado = self.request.query_params.get('estado')
        if delegacion_id:
            qs = qs.filter(delegacion_id=delegacion_id)
        if responsable_id:
            qs = qs.filter(responsable_id=responsable_id)
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    def perform_create(self, serializer):
        if self.request.user.esta_limitado_a_delegacion:
            delegacion = serializer.validated_data.get('delegacion')
            responsable = serializer.validated_data.get('responsable')
            if (
                not self.request.user.delegacion_id
                or not delegacion
                or delegacion.pk != self.request.user.delegacion_id
                or (responsable and responsable.delegacion_id != self.request.user.delegacion_id)
            ):
                raise DRFPermissionDenied('Solo puedes crear compromisos dentro de tu delegación.')
        compromiso = serializer.save()
        registrar_evento_auditoria(
            self.request,
            self.request.user if self.request.user.is_authenticated else compromiso.responsable,
            'CREAR', 'CompromisoAgenda', compromiso.id, None, {'solicitante': compromiso.solicitante}
        )

    def perform_update(self, serializer):
        if self.request.user.esta_limitado_a_delegacion:
            delegacion = serializer.validated_data.get('delegacion', serializer.instance.delegacion)
            responsable = serializer.validated_data.get('responsable', serializer.instance.responsable)
            if (
                delegacion.pk != self.request.user.delegacion_id
                or (responsable and responsable.delegacion_id != self.request.user.delegacion_id)
            ):
                raise DRFPermissionDenied('Solo puedes mantener compromisos dentro de tu delegación.')
        serializer.save()

    @action(detail=True, methods=['post'], url_path='cambiar-estado')
    def cambiar_estado(self, request, pk=None):
        compromiso = self.get_object()
        nuevo_estado = request.data.get('estado')
        observacion = (request.data.get('observacion') or '').strip()
        if nuevo_estado not in {
            CompromisoAgenda.Estado.INGRESADO,
            CompromisoAgenda.Estado.PENDIENTE,
            CompromisoAgenda.Estado.EN_PROCESO,
            CompromisoAgenda.Estado.REPROGRAMADO,
            CompromisoAgenda.Estado.REALIZADO,
        }:
            raise DRFValidationError({'estado': f'Estado inválido: {nuevo_estado}'})
        if not observacion:
            raise DRFValidationError({
                'observacion': 'Explica el cambio de estado en la observación.'
            })

        with transaction.atomic():
            compromiso = self.get_queryset().select_for_update().get(pk=compromiso.pk)
            self.check_object_permissions(request, compromiso)
            estado_anterior = compromiso.estado
            try:
                compromiso.registrar_transicion(
                    nuevo_estado,
                    request.user,
                    observacion,
                )
            except DjangoValidationError as exc:
                raise DRFValidationError(exc.message_dict) from exc
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='CAMBIO_ESTADO',
                evento='MODIFICACION',
                entidad='CompromisoAgenda',
                identificador=str(compromiso.pk),
                identificador_registro=str(compromiso.pk),
                valor_anterior={'estado': estado_anterior},
                valor_nuevo={
                    'estado': compromiso.estado,
                    'fecha_realizacion': (
                        compromiso.fecha_realizacion.isoformat()
                        if compromiso.fecha_realizacion
                        else None
                    ),
                    'observacion': observacion,
                },
                ip_origen=request.META.get('REMOTE_ADDR', '127.0.0.1'),
            )

        return Response(CompromisoAgendaSerializer(compromiso).data)


class ActividadViewSet(viewsets.ModelViewSet):
    """
    CRUD del registro de actividades diarias de los funcionarios.
    """
    queryset = Actividad.objects.all()
    serializer_class = ActividadSerializer

    def get_queryset(self):
        qs = Actividad.objects.all().select_related('funcionario', 'delegacion', 'item_medicion', 'compromiso_agenda').prefetch_related('evidencias')
        if self.request.user.es_administrativo:
            qs = qs.filter(funcionario=self.request.user)
        elif self.request.user.esta_limitado_a_delegacion:
            qs = qs.filter(delegacion_id=self.request.user.delegacion_id)
        delegacion_id = self.request.query_params.get('delegacion')
        funcionario_id = self.request.query_params.get('funcionario')
        fecha_desde = self.request.query_params.get('fecha_desde')
        fecha_hasta = self.request.query_params.get('fecha_hasta')

        if delegacion_id:
            qs = qs.filter(delegacion_id=delegacion_id)
        if funcionario_id:
            qs = qs.filter(funcionario_id=funcionario_id)
        if fecha_desde:
            qs = qs.filter(fecha_actividad__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha_actividad__lte=fecha_hasta)
        return qs

    def create(self, request, *args, **kwargs):
        # Permite registrar actividad y evidencia en una sola operación optimizada para móvil (<= 3 clics)
        data = request.data.copy()
        if request.user.es_administrativo:
            if data.get('funcionario') not in (None, '', str(request.user.pk), request.user.pk):
                return Response({'error': 'Solo puedes registrar actividades propias.'}, status=status.HTTP_403_FORBIDDEN)
            if data.get('delegacion') not in (None, '', str(request.user.delegacion_id), request.user.delegacion_id):
                return Response({'error': 'Solo puedes registrar actividades en tu delegación.'}, status=status.HTTP_403_FORBIDDEN)
        elif request.user.esta_limitado_a_delegacion and data.get('delegacion') not in (
            None, '', str(request.user.delegacion_id), request.user.delegacion_id
        ):
            return Response({'error': 'Solo puedes registrar actividades en tu delegación.'}, status=status.HTTP_403_FORBIDDEN)
        if request.user.esta_limitado_a_delegacion and data.get('funcionario'):
            try:
                funcionario = Usuario.objects.get(pk=data['funcionario'])
            except (Usuario.DoesNotExist, TypeError, ValueError):
                return Response({'error': 'Funcionario no encontrado.'}, status=status.HTTP_400_BAD_REQUEST)
            if funcionario.delegacion_id != request.user.delegacion_id:
                return Response({'error': 'Solo puedes registrar actividades de tu delegación.'}, status=status.HTTP_403_FORBIDDEN)
        
        # Si el funcionario no viene en data, usar el logueado o fallback
        funcionario_id = data.get('funcionario')
        if not funcionario_id and request.user.is_authenticated:
            data['funcionario'] = request.user.id
            if not data.get('delegacion') and request.user.delegacion:
                data['delegacion'] = request.user.delegacion.id

        fotos_multiples = request.FILES.getlist('fotos')
        archivos_individuales = [
            request.FILES[campo]
            for campo in ('archivo_foto', 'archivo_foto_2', 'archivo_foto_3')
            if campo in request.FILES
        ]
        archivos_subidos = fotos_multiples or archivos_individuales
        try:
            for archivo in archivos_subidos:
                validar_archivo_evidencia(archivo)
        except DjangoValidationError as exc:
            raise DRFValidationError({'fotos': exc.messages}) from exc

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        actividad = serializer.save()

        # Generar evidencia vinculada de inmediato con su código alfanumérico único
        foto_archivo = request.FILES.get('archivo_foto')
        foto_archivo_2 = request.FILES.get('archivo_foto_2')
        foto_archivo_3 = request.FILES.get('archivo_foto_3')
        url_simulada = data.get('url_foto_simulada') or ''
        url_simulada_2 = data.get('url_foto_simulada_2') or ''
        url_simulada_3 = data.get('url_foto_simulada_3') or ''

        if fotos_multiples:
            evidencias = [
                Evidencia.objects.create(
                    actividad=actividad,
                    archivo_foto=foto,
                    subido_por=request.user,
                    metadatos=metadatos_archivo_evidencia(foto, request.user),
                    estado=Evidencia.Estado.PENDIENTE,
                )
                for foto in fotos_multiples
            ]
            evidencia = evidencias[0]
        else:
            evidencia = Evidencia.objects.create(
                actividad=actividad,
                archivo_foto=foto_archivo,
                archivo_foto_2=foto_archivo_2,
                archivo_foto_3=foto_archivo_3,
                subido_por=request.user,
                metadatos={
                    'archivos': [
                        metadatos_archivo_evidencia(archivo, request.user)
                        for archivo in archivos_individuales
                    ]
                },
                url_foto_simulada=url_simulada or 'https://images.unsplash.com/photo-1541888946425-d0fbb1861564?auto=format&fit=crop&w=600&q=80',
                url_foto_simulada_2=url_simulada_2,
                url_foto_simulada_3=url_simulada_3,
                estado=Evidencia.Estado.PENDIENTE
            )

        registrar_evento_auditoria(
            request,
            actividad.funcionario,
            'CREAR', 'Actividad', actividad.id,
            None, {'actividad': actividad.actividad_solicitud, 'codigo_evidencia': evidencia.codigo_verificador}
        )

        headers = self.get_success_headers(serializer.data)
        # Recargar para incluir evidencia
        return Response(ActividadSerializer(actividad).data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['post'], url_path='anular')
    def anular(self, request, pk=None):
        if not (
            request.user.is_superuser
            or request.user.es_admin()
            or request.user.es_supervisor
        ):
            raise DRFPermissionDenied(
                'Solo supervisores y administradores pueden anular actividades.'
            )
        motivo = (request.data.get('motivo') or '').strip()
        if not motivo:
            raise DRFValidationError({'motivo': 'Indica el motivo de la anulación.'})

        with transaction.atomic():
            try:
                actividad = self.get_queryset().select_for_update().get(pk=pk)
            except Actividad.DoesNotExist as exc:
                raise NotFound() from exc
            self.check_object_permissions(request, actividad)
            estado_anterior = {
                'anulada': actividad.anulada,
                'motivo_anulacion': actividad.motivo_anulacion,
            }
            try:
                actividad.anular(request.user, motivo)
            except DjangoValidationError as exc:
                raise DRFValidationError(exc.message_dict) from exc
            recalcular_indicadores_actividad(actividad)
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='ANULAR_ACTIVIDAD',
                evento='MODIFICACION',
                entidad='Actividad',
                identificador=str(actividad.pk),
                identificador_registro=str(actividad.pk),
                valor_anterior=estado_anterior,
                valor_nuevo={
                    'anulada': actividad.anulada,
                    'motivo_anulacion': actividad.motivo_anulacion,
                    'codigo_registro': actividad.codigo_registro,
                },
                ip_origen=request.META.get('REMOTE_ADDR', '127.0.0.1'),
            )
        return Response(ActividadSerializer(actividad).data)


class EvidenciaViewSet(viewsets.ModelViewSet):
    """
    Gestión de Evidencias y módulo de validación ("Check" del Delegado/Verificador).
    """
    queryset = Evidencia.objects.all().select_related('actividad', 'verificador')
    serializer_class = EvidenciaSerializer

    def perform_create(self, serializer):
        actividad = serializer.validated_data['actividad']
        with transaction.atomic():
            actividad = Actividad.objects.select_for_update().get(pk=actividad.pk)
            if actividad.anulada:
                raise DRFValidationError({
                    'actividad': 'No se puede asociar evidencia a una actividad anulada.'
                })
            if Evidencia.objects.filter(
                actividad=actividad,
                estado=Evidencia.Estado.APROBADA,
            ).exists():
                raise DRFValidationError({
                    'actividad': 'La actividad está cerrada porque ya tiene una evidencia aprobada.'
                })
            serializer.save()

    def get_queryset(self):
        qs = Evidencia.objects.all()
        if self.request.user.es_administrativo:
            qs = qs.filter(actividad__funcionario=self.request.user)
        elif self.request.user.esta_limitado_a_delegacion:
            qs = qs.filter(actividad__delegacion_id=self.request.user.delegacion_id)
        delegacion_id = self.request.query_params.get('delegacion')
        estado = self.request.query_params.get('estado')
        if delegacion_id:
            qs = qs.filter(actividad__delegacion_id=delegacion_id)
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    @action(detail=True, methods=['post'], url_path='validar')
    def validar(self, request, pk=None):
        """
        Botón de validación del Delegado: Aprobar, Rechazar o Corrección.
        """
        if not (
            request.user.is_superuser
            or request.user.es_admin()
            or request.user.es_supervisor
            or request.user.esta_limitado_a_delegacion
        ):
            raise DRFPermissionDenied(
                'Solo supervisores, delegados y administradores pueden validar evidencias.'
            )

        decision = request.data.get('decision')
        observacion = (request.data.get('observacion') or '').strip()
        if decision not in Evidencia.Estado.values:
            return Response(
                {'error': f'Decisión no válida: {decision}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if decision in (
            Evidencia.Estado.RECHAZADA,
            Evidencia.Estado.CORRECCION,
        ) and not observacion:
            return Response(
                {'error': 'Indica el motivo del rechazo o la corrección solicitada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            try:
                evidencia = self.get_queryset().select_for_update().get(pk=pk)
            except Evidencia.DoesNotExist as exc:
                raise NotFound() from exc
            self.check_object_permissions(request, evidencia)
            if evidencia.actividad.anulada:
                raise DRFValidationError({
                    'actividad': 'No se puede validar evidencia de una actividad anulada.'
                })
            estado_anterior = evidencia.estado
            evidencia.estado = decision
            evidencia.observacion = observacion
            evidencia.observacion_rechazo = (
                observacion
                if decision in (
                    Evidencia.Estado.RECHAZADA,
                    Evidencia.Estado.CORRECCION,
                )
                else None
            )
            evidencia.verificador = request.user
            evidencia.fecha_validacion = timezone.now()
            evidencia.save()
            recalcular_indicadores_actividad(evidencia.actividad)

            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='VALIDAR_EVIDENCIA',
                evento=(
                    'APROBACION'
                    if decision == Evidencia.Estado.APROBADA
                    else 'MODIFICACION'
                ),
                entidad='Evidencia',
                identificador=str(evidencia.pk),
                identificador_registro=str(evidencia.pk),
                valor_anterior={'estado': estado_anterior},
                valor_nuevo={
                    'estado': decision,
                    'observacion': observacion,
                    'codigo': evidencia.codigo_verificador,
                    'verificador_id': request.user.pk,
                    'fecha_validacion': evidencia.fecha_validacion.isoformat(),
                },
                ip_origen=request.META.get('REMOTE_ADDR', '127.0.0.1'),
            )

        return Response(EvidenciaSerializer(evidencia).data)

class MetricasAPIView(APIView):
    """
    Endpoints analíticos de métricas del semáforo.
    """
    def get(self, request):
        tipo = request.query_params.get('tipo', 'comunal') # comunal, delegacion, funcionario
        periodo_id = request.query_params.get('periodo')
        
        if periodo_id:
            periodo = PeriodoMedicion.objects.filter(id=periodo_id).first()
        else:
            periodo = PeriodoMedicion.objects.filter(cerrado=False).order_by('-fecha_inicio', '-id').first() or PeriodoMedicion.objects.order_by('-fecha_inicio', '-id').first()

        if not periodo:
            return Response({'error': 'No hay períodos de medición registrados.'}, status=status.HTTP_404_NOT_FOUND)

        if tipo == 'comunal':
            data = calcular_resumen_comunal(periodo)
            return Response(data)

        elif tipo == 'delegacion':
            delegacion_id = request.query_params.get('delegacion_id')
            if not delegacion_id:
                return Response({'error': 'delegacion_id requerido'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                delegacion = Delegacion.objects.get(id=delegacion_id)
            except Delegacion.DoesNotExist:
                return Response({'error': 'Delegación no encontrada'}, status=status.HTTP_404_NOT_FOUND)
            if request.user.esta_limitado_a_delegacion and delegacion.pk != request.user.delegacion_id:
                return Response({'error': 'Acceso restringido a tu delegación.'}, status=status.HTTP_403_FORBIDDEN)
            if request.user.es_administrativo:
                return Response({'error': 'No tienes permiso para consultar métricas de delegación.'}, status=status.HTTP_403_FORBIDDEN)
            data = calcular_resumen_delegacion(delegacion, periodo)
            return Response(data)

        elif tipo == 'funcionario':
            funcionario_id = request.query_params.get('funcionario_id')
            if not funcionario_id:
                return Response({'error': 'funcionario_id requerido'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                funcionario = Usuario.objects.get(id=funcionario_id)
            except Usuario.DoesNotExist:
                return Response({'error': 'Funcionario no encontrado'}, status=status.HTTP_404_NOT_FOUND)
            if request.user.es_administrativo and funcionario.pk != request.user.pk:
                return Response({'error': 'Solo puedes consultar tus propias métricas.'}, status=status.HTTP_403_FORBIDDEN)
            if request.user.esta_limitado_a_delegacion and funcionario.delegacion_id != request.user.delegacion_id:
                return Response({'error': 'Acceso restringido a funcionarios de tu delegación.'}, status=status.HTTP_403_FORBIDDEN)
            data = calcular_metricas_funcionario(funcionario, periodo)
            return Response(data)

        return Response({'error': 'Tipo no reconocido'}, status=status.HTTP_400_BAD_REQUEST)


class CasoSocialViewSet(viewsets.ModelViewSet):
    queryset = CasoSocial.objects.all().prefetch_related('atenciones')
    serializer_class = CasoSocialSerializer

    def get_queryset(self):
        qs = CasoSocial.objects.all().prefetch_related('atenciones')
        if self.request.user.es_administrativo:
            return qs.none()
        if self.request.user.esta_limitado_a_delegacion:
            return qs.filter(delegacion_id=self.request.user.delegacion_id)
        return qs


class AtencionSocialViewSet(viewsets.ModelViewSet):
    queryset = AtencionSocial.objects.all()
    serializer_class = AtencionSocialSerializer

    def get_queryset(self):
        qs = AtencionSocial.objects.all()
        if self.request.user.es_administrativo:
            return qs.none()
        if self.request.user.esta_limitado_a_delegacion:
            return qs.filter(caso__delegacion_id=self.request.user.delegacion_id)
        return qs

    def perform_create(self, serializer):
        caso = serializer.validated_data['caso']
        with transaction.atomic():
            try:
                caso = CasoSocial.objects.select_for_update().get(pk=caso.pk)
            except CasoSocial.DoesNotExist as exc:
                raise NotFound() from exc
            if caso.cerrado:
                raise DRFValidationError({
                    'caso': 'No se pueden registrar gestiones en un caso cerrado.'
                })
            siguiente_etapa = (
                caso.atenciones.order_by('-etapa')
                .values_list('etapa', flat=True)
                .first()
                or 0
            ) + 1
            if siguiente_etapa > 3:
                raise DRFValidationError({
                    'caso': 'El caso ya tiene el máximo de tres gestiones.'
                })
            if serializer.validated_data['etapa'] != siguiente_etapa:
                raise DRFValidationError({
                    'etapa': f'La siguiente gestión disponible es la etapa {siguiente_etapa}.'
                })
            try:
                atencion = serializer.save(
                    caso=caso,
                    funcionario=self.request.user,
                )
            except ValueError as exc:
                raise DRFValidationError({'caso': str(exc)}) from exc
            RegistroAuditoria.objects.create(
                usuario=self.request.user,
                funcionario=self.request.user,
                accion='REGISTRAR_ATENCION_SOCIAL',
                evento='CREACION',
                entidad='AtencionSocial',
                identificador=str(atencion.pk),
                identificador_registro=str(atencion.pk),
                valor_nuevo={
                    'caso_id': caso.pk,
                    'etapa': atencion.etapa,
                    'tipo_atencion': atencion.tipo_atencion,
                },
                ip_origen=self.request.META.get('REMOTE_ADDR', '127.0.0.1'),
            )


class RegistroAuditoriaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RegistroAuditoria.objects.all()
    serializer_class = RegistroAuditoriaSerializer
    permission_classes = [AuditAccessPermission]
