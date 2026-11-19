import csv
from math import isfinite
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.utils import timezone
from django.utils.dateparse import parse_date
from datetime import date, datetime, timedelta
import re
import base64
import binascii
import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.contrib.auth.decorators import login_not_required, login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView, UpdateView
from django.urls import reverse_lazy
from sgr_app.forms import (
    AjusteDecisionForm,
    AjusteIncentivoForm,
    ActividadTerrenoForm,
    AtencionSocialForm,
    CasoSocialForm,
    CompromisoAgendaForm,
    ComentarioTextoForm,
    CompromisoEstadoForm,
    InitialPasswordForm,
    PasswordResetCodeForm,
    PasswordResetRequestForm,
    PasswordResetSetPasswordForm,
    MiFichaPersonalForm,
    SolicitanteForm,
    ValidarEvidenciaForm,
)
from sgr_app.periodos import get_nombre_trimestre_actual, get_periodo_trimestre_actual
from sgr_app.services import seguridad_acceso
from sgr_app.services.seguridad_acceso import obtener_ip_cliente
from sgr_app.concurrencia import ConcurrenciaContextMixin
from sgr_app.services.configuracion import parametros_calculo
from sgr_app.services.panel_territorial import (
    comparacion_territorial,
    panel_territorial,
    proximos_vencimientos,
)
from sgr_app.models import (
    AjusteIncentivo, ConceptoAjuste, Delegacion, Cargo, Area, Usuario, PeriodoMedicion, ItemMedicion,
    Actividad, ComentarioActividad, Evidencia, CompromisoAgenda, CasoSocial, AtencionSocial, Solicitante,
    RegistroAuditoria, MetaModel, ObservacionCompromiso, CatalogoServicio,
    PasswordResetCode, TipoActividad, TipoAtencionSocial, SubatencionSocial,
    AlertaRevisada, ReglaAlerta,
)
from sgr_app.services.alertas import (
    REGLAS_POR_DEFECTO,
    aplica_a,
    reglas_de_alertas_generadas,
    reglas_vigentes,
    registrar_alertas_generadas,
)
from sgr_app.services.calculos import (
    calcular_dias_periodo,
    calcular_metricas_funcionario,
    calcular_resumen_delegacion,
    calcular_resumen_comunal,
    determinar_color_semaforo,
    recalcular_indicadores_actividad,
)
from sgr_app.services.evidencias import (
    MAX_FOTOS_POR_REGISTRO,
    MENSAJE_MAX_FOTOS,
    metadatos_archivo_evidencia,
    validar_archivo_evidencia,
)
from sgr_app.validators import normalize_chilean_phone

ACTIVIDAD_TERRITORIOS = (
    'Sector Centro', 'El Milagro', 'La Pampa', 'Las Compañías',
    'La Antena', 'Avenida del Mar', 'Sector Rural', 'Otro',
)
ACTIVIDAD_AREAS_APOYO = (
    'Operaciones y Territorio', 'Alumbrado Público', 'Aseo y Ornato',
    'DIDECO / Social', 'Tránsito y Transporte', 'Seguridad Ciudadana',
    'Medio Ambiente', 'SECPLAN / Proyectos',
)

PASSWORD_RESET_CODE_TTL = timedelta(minutes=10)
PASSWORD_RESET_RESEND_DELAY = timedelta(seconds=60)
PASSWORD_RESET_MAX_ATTEMPTS = 5


@login_not_required
def web_password_reset_request(request):
    if request.method == 'POST':
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].strip().lower()
            user = Usuario.objects.filter(
                email__iexact=email,
                is_active=True,
            ).first()
            request.session.pop('password_reset_otp_id', None)

            if user:
                now = timezone.now()
                code = None
                send_code = False
                with transaction.atomic():
                    user = Usuario.objects.select_for_update().get(pk=user.pk)
                    existing = PasswordResetCode.objects.select_for_update().filter(
                        user=user,
                    ).first()
                    if not existing or existing.created_at <= now - PASSWORD_RESET_RESEND_DELAY:
                        if existing:
                            existing.invalidar('REEMPLAZADO')
                        code = f'{secrets.randbelow(1_000_000):06d}'
                        send_code = True
                        reset_code = PasswordResetCode.objects.create(
                            user=user,
                            code_hash=make_password(code),
                            expires_at=now + PASSWORD_RESET_CODE_TTL,
                        )
                    else:
                        reset_code = existing

                request.session['password_reset_otp_id'] = reset_code.pk
                request.session.set_expiry(PASSWORD_RESET_CODE_TTL)
                if send_code:
                    send_mail(
                        subject='Código para recuperar tu contraseña - SGR',
                        message=(
                            f'Hola {user.get_full_name() or user.username},\n\n'
                            'Tu código de recuperación es: '
                            f'{code[:3]} {code[3:]}\n\n'
                            'Es de un solo uso y vence en 10 minutos. '
                            'Si no solicitaste este cambio, ignora este correo.'
                        ),
                        from_email=None,
                        recipient_list=[user.email],
                        fail_silently=False,
                    )

            return redirect('password_reset_done')
    else:
        form = PasswordResetRequestForm()

    return render(request, 'registration/password_reset_form.html', {'form': form})


@login_not_required
def web_password_reset_done(request):
    if request.method == 'POST':
        form = PasswordResetCodeForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['code']
            now = timezone.now()
            reset_id = request.session.get('password_reset_otp_id')
            reset_request = PasswordResetCode.objects.select_related('user').filter(
                pk=reset_id,
                user__is_active=True,
                verified_at__isnull=True,
            ).first() if reset_id else None

            if reset_request:
                with transaction.atomic():
                    reset_request = PasswordResetCode.objects.select_for_update().filter(
                        pk=reset_request.pk,
                    ).first()
                    now = timezone.now()
                    if (
                        reset_request
                        and reset_request.expires_at > now
                        and reset_request.attempts < PASSWORD_RESET_MAX_ATTEMPTS
                        and check_password(code, reset_request.code_hash)
                    ):
                        reset_request.verified_at = now
                        reset_request.code_hash = ''
                        reset_request.save(update_fields=['verified_at', 'code_hash'])
                        request.session['password_reset_otp_id'] = reset_request.pk
                        request.session.set_expiry(PASSWORD_RESET_CODE_TTL)
                        return redirect('password_reset_change')
                    if (
                        reset_request
                        and reset_request.attempts < PASSWORD_RESET_MAX_ATTEMPTS
                    ):
                        reset_request.attempts += 1
                        reset_request.save(update_fields=['attempts'])

            if reset_request and reset_request.expires_at <= now:
                request.session.pop('password_reset_otp_id', None)

            form.add_error(
                None,
                'El código no es válido, venció o superó los intentos permitidos.',
            )
    else:
        form = PasswordResetCodeForm()

    return render(request, 'registration/password_reset_done.html', {'form': form})


@login_not_required
def web_password_reset_change(request):
    reset_id = request.session.get('password_reset_otp_id')
    reset_request = PasswordResetCode.objects.select_related('user').filter(
        pk=reset_id,
        user__is_active=True,
    ).first() if reset_id else None
    now = timezone.now()

    if (
        not reset_request
        or not reset_request.verified_at
        or reset_request.verified_at <= now - PASSWORD_RESET_CODE_TTL
        or reset_request.expires_at <= now
    ):
        request.session.pop('password_reset_otp_id', None)
        messages.error(request, 'El código de recuperación venció. Solicita uno nuevo.')
        return redirect('password_reset')

    if request.method == 'POST':
        form = PasswordResetSetPasswordForm(request.POST, user=reset_request.user)
        if form.is_valid():
            with transaction.atomic():
                reset_request = PasswordResetCode.objects.select_for_update().select_related(
                    'user',
                ).filter(pk=reset_id).first()
                now = timezone.now()
                if (
                    not reset_request
                    or not reset_request.verified_at
                    or reset_request.verified_at <= now - PASSWORD_RESET_CODE_TTL
                    or reset_request.expires_at <= now
                ):
                    request.session.pop('password_reset_otp_id', None)
                    messages.error(request, 'El código de recuperación venció. Solicita uno nuevo.')
                    return redirect('password_reset')

                reset_request.user.set_password(form.cleaned_data['new_password1'])
                # La persona eligió una clave propia al verificar su correo:
                # ya no necesita pasar por el cambio de clave inicial.
                reset_request.user.must_change_password = False
                reset_request.user.initial_login_expires_at = None
                reset_request.user.save(update_fields=[
                    'password',
                    'must_change_password',
                    'initial_login_expires_at',
                ])
                reset_request.invalidar('USADO')

            request.session.pop('password_reset_otp_id', None)
            return redirect('password_reset_complete')
    else:
        form = PasswordResetSetPasswordForm(user=reset_request.user)

    return render(request, 'registration/password_reset_confirm.html', {'form': form})


@login_not_required
def web_password_reset_complete(request):
    return render(request, 'registration/password_reset_complete.html')


def es_codigo_2fa_valido(user, code):
    return codigo_totp_valido(getattr(user, 'totp_secret', None), code)


def codigo_totp_valido(secret, code):
    """Valida un código TOTP (RFC 6238, 30 s, 6 dígitos, ±1 intervalo)."""
    if not secret or not re.fullmatch(r'\d{6}', code or ''):
        return False
    try:
        key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8), casefold=True)
    except (binascii.Error, ValueError):
        return False
    counter = int(time.time()) // 30
    for candidate in range(counter - 1, counter + 2):
        digest = hmac.new(key, candidate.to_bytes(8, 'big'), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        value = int.from_bytes(digest[offset:offset + 4], 'big') & 0x7FFFFFFF
        expected = f'{value % 1_000_000:06d}'
        if hmac.compare_digest(expected, code):
            return True
    return False

def get_periodo_activo():
    periodo, _ = get_periodo_trimestre_actual()
    return periodo


def _puede_gestionar_compromiso(user, compromiso=None):
    if user.es_consulta:
        return False
    if user.es_admin() or user.es_supervisor:
        return True
    if user.esta_limitado_a_delegacion:
        return bool(user.delegacion_id and compromiso and compromiso.delegacion_id == user.delegacion_id)
    return bool(
        user.es_administrativo
        and compromiso
        and compromiso.creado_por_id == user.pk
        and compromiso.responsable_id == user.pk
    )


def _resumen_comunal_visible(user, periodo):
    if not periodo:
        return None

    if user.esta_limitado_a_delegacion:
        if not user.delegacion_id:
            return None
        resumen = calcular_resumen_delegacion(
            user.delegacion,
            periodo,
        )
        info_tiempo = calcular_dias_periodo(periodo)
        return {
            'periodo_nombre': periodo.nombre,
            'info_tiempo': info_tiempo,
            'meta_esperada_pct': info_tiempo['meta_esperada_pct'],
            'promedio_comunal_pct': resumen['promedio_ponderado_pct'],
            'semaforo_comunal': resumen['semaforo_delegacion'],
            'total_evidencias_pendientes': resumen['evidencias_pendientes_check'],
            'total_compromisos_vencidos': resumen['compromisos']['vencidos'],
            'total_ingresos_comunales': resumen['total_ingresos'],
            'delegaciones': [resumen],
        }

    return calcular_resumen_comunal(periodo)


def _celda_csv_segura(valor):
    """Evita que un texto ingresado por usuarios se ejecute como fórmula en Excel."""
    texto = '' if valor is None else str(valor)
    if texto and texto[0] in ('=', '+', '-', '@', chr(9), chr(13)):
        return f"'{texto}"
    return texto


def _filtrar_por_delegacion(queryset, user, campo='delegacion_id'):
    if not user.esta_limitado_a_delegacion:
        return queryset
    if not user.delegacion_id:
        return queryset.none()
    return queryset.filter(**{campo: user.delegacion_id})


def _agrupar_funcionarios_por_cargo(funcionarios):
    grupos = {}
    for funcionario in funcionarios:
        cargo_id = funcionario.get('cargo_id')
        clave = cargo_id if cargo_id is not None else 'sin-cargo'
        grupo = grupos.setdefault(clave, {
            'cargo_id': cargo_id,
            'cargo': funcionario.get('cargo') or 'Sin Cargo',
            'funcionarios': [],
        })
        grupo['funcionarios'].append(funcionario)

    resumenes = []
    for grupo in grupos.values():
        personal = grupo['funcionarios']
        grupo['total_funcionarios'] = len(personal)
        grupo['promedio_ponderado_pct'] = round(
            sum(funcionario['total_ponderado_pct'] for funcionario in personal)
            / len(personal),
            2,
        )
        grupo['meta_esperada_pct'] = round(
            sum(funcionario.get('meta_esperada_pct', 0) for funcionario in personal)
            / len(personal),
            2,
        )
        grupo['brecha_pct'] = round(
            grupo['promedio_ponderado_pct'] - grupo['meta_esperada_pct'],
            2,
        )
        resumenes.append(grupo)
    return sorted(resumenes, key=lambda grupo: grupo['cargo'].casefold())


def _puede_gestionar_ajustes(user):
    return bool(
        user.is_authenticated
        and (
            user.is_superuser
            or user.es_admin()
            or user.es_supervisor
        )
    )


def _snapshot_ajuste(ajuste):
    return {
        'funcionario_id': ajuste.funcionario_id,
        'periodo_id': ajuste.periodo_id,
        'tipo': ajuste.tipo,
        'motivo': ajuste.motivo,
        'valor_porcentaje': ajuste.valor_porcentaje,
        'estado': ajuste.estado,
        'registrado_por_id': ajuste.registrado_por_id,
        'aprobado_por_id': ajuste.aprobadoPor_id,
        'fecha_aprobacion': (
            ajuste.fechaAprobacion.isoformat()
            if ajuste.fechaAprobacion
            else None
        ),
    }


@login_required(login_url='web_login')
def web_ajustes_incentivo(request):
    if not _puede_gestionar_ajustes(request.user):
        raise PermissionDenied

    periodo_actual, _ = get_periodo_trimestre_actual()
    periodos = PeriodoMedicion.objects.order_by('-fecha_inicio', '-pk')
    periodo_param = (
        request.POST.get('periodo', '')
        if request.method == 'POST'
        else request.GET.get('periodo', '')
    )
    periodo = periodo_actual
    periodo_seleccionado = str(periodo_actual.pk) if periodo_actual.pk else ''
    if periodo_param:
        periodo_elegido = (
            periodos.filter(pk=int(periodo_param)).first()
            if periodo_param.isdecimal()
            else None
        )
        if periodo_elegido:
            periodo = periodo_elegido
            periodo_seleccionado = str(periodo.pk)
        else:
            messages.error(request, 'El período seleccionado no existe.')

    funcionarios = Usuario.objects.filter(
        rol=Usuario.Rol.ADMINISTRATIVO,
        es_delegado=False,
        is_active=True,
    ).select_related('delegacion', 'cargo').order_by('last_name', 'first_name', 'username')
    conceptos = ConceptoAjuste.objects.filter(activo=True).order_by('nombre')
    ajuste_form = AjusteIncentivoForm(
        request.POST if request.method == 'POST' else None,
        funcionarios=funcionarios,
        conceptos=conceptos,
    )
    if request.method == 'POST':
        if not periodo.pk:
            ajuste_form.add_error(None, 'Debes configurar un período antes de registrar un ajuste.')
        elif periodo.cerrado:
            ajuste_form.add_error(None, 'No se pueden registrar ajustes en un período cerrado.')
        elif ajuste_form.is_valid():
            funcionario = ajuste_form.cleaned_data['funcionario']
            ajuste = AjusteIncentivo(
                funcionario=funcionario,
                registrado_por=request.user,
                periodo=periodo,
                concepto=ajuste_form.cleaned_data['concepto'],
                tipo=ajuste_form.cleaned_data['concepto'].nombre,
                motivo=ajuste_form.cleaned_data['motivo'],
                valor_porcentaje=ajuste_form.cleaned_data['valor_porcentaje'],
                estado='PENDIENTE',
            )
            try:
                ajuste.full_clean()
                with transaction.atomic():
                    ajuste.save()
                    RegistroAuditoria.objects.create(
                        usuario=request.user,
                        funcionario=request.user,
                        evento='CREACION',
                        accion='CREAR_AJUSTE_INCENTIVO',
                        entidad='AjusteIncentivo',
                        identificador=str(ajuste.pk),
                        identificador_registro=str(ajuste.pk),
                        valor_nuevo=_snapshot_ajuste(ajuste),
                        ip_origen=obtener_ip_cliente(request) or None,
                    )
            except ValidationError as error:
                if hasattr(error, 'message_dict'):
                    for field_name, errors in error.message_dict.items():
                        ajuste_form.add_error(field_name if field_name in ajuste_form.fields else None, errors)
                else:
                    ajuste_form.add_error(None, error.messages)
            else:
                messages.success(request, 'El ajuste quedó registrado y pendiente de aprobación.')
                return redirect(f'{reverse("web_ajustes_incentivo")}?periodo={periodo.pk}')

    ajustes = list(
        AjusteIncentivo.objects.filter(periodo=periodo)
        .select_related('funcionario', 'funcionario__delegacion', 'registrado_por', 'aprobadoPor')
        .order_by('-fecha_registro', '-pk')
    ) if periodo.pk else []
    ajustes_por_id = {}
    for funcionario_id in {ajuste.funcionario_id for ajuste in ajustes}:
        funcionario = next(
            ajuste.funcionario for ajuste in ajustes
            if ajuste.funcionario_id == funcionario_id
        )
        metricas = calcular_metricas_funcionario(funcionario, periodo)
        ajustes_por_id.update({
            detalle['id']: detalle
            for detalle in metricas['ajustes']
        })
    for ajuste in ajustes:
        detalle = ajustes_por_id.get(ajuste.pk, {})
        ajuste.efecto_pct = detalle.get('efecto_pct', 0)
        ajuste.resultado_pct = detalle.get('resultado_pct')

    return render(request, 'ajustes_incentivo.html', {
        'periodo': periodo,
        'periodos': periodos,
        'periodo_seleccionado': periodo_seleccionado,
        'funcionarios': funcionarios,
        'ajuste_form': ajuste_form,
        'ajustes': ajustes,
        'periodo_cerrado': bool(periodo.pk and periodo.cerrado),
    })


@login_required(login_url='web_login')
@require_POST
def web_decidir_ajuste_incentivo(request, ajuste_id):
    if not _puede_gestionar_ajustes(request.user):
        raise PermissionDenied

    form = AjusteDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            '; '.join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )
        return redirect('web_ajustes_incentivo')

    with transaction.atomic():
        ajuste = get_object_or_404(
            AjusteIncentivo.objects.select_for_update().select_related('periodo'),
            pk=ajuste_id,
        )
        if ajuste.estado != 'PENDIENTE':
            messages.error(request, 'Este ajuste ya fue revisado.')
            return redirect(f'{reverse("web_ajustes_incentivo")}?periodo={ajuste.periodo_id}')
        valor_anterior = _snapshot_ajuste(ajuste)
        try:
            if form.cleaned_data['decision'] == 'APROBAR':
                ajuste.aprobarAjuste(request.user)
                accion = 'APROBAR_AJUSTE_INCENTIVO'
            else:
                ajuste.rechazarAjuste(
                    request.user,
                    form.cleaned_data['observacion'].strip(),
                )
                accion = 'RECHAZAR_AJUSTE_INCENTIVO'
        except (PermissionError, ValueError, ValidationError) as error:
            messages.error(request, str(error))
            return redirect(f'{reverse("web_ajustes_incentivo")}?periodo={ajuste.periodo_id}')

        RegistroAuditoria.objects.create(
            usuario=request.user,
            funcionario=request.user,
            evento='APROBACION' if ajuste.estado == 'APROBADO' else 'MODIFICACION',
            accion=accion,
            entidad='AjusteIncentivo',
            identificador=str(ajuste.pk),
            identificador_registro=str(ajuste.pk),
            valor_anterior=valor_anterior,
            valor_nuevo=_snapshot_ajuste(ajuste),
            ip_origen=obtener_ip_cliente(request) or None,
        )

    messages.success(request, 'El ajuste fue revisado correctamente.')
    return redirect(f'{reverse("web_ajustes_incentivo")}?periodo={ajuste.periodo_id}')


@login_not_required
def web_login(request):
    if request.user.is_authenticated:
        return redirect('web_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        code_2fa = request.POST.get('code_2fa', '').strip()

        if not username and not password:
            return render(request, 'login.html', {'error': 'Usuario y contraseña son obligatorios.'})
        if not username:
            return render(request, 'login.html', {'error': 'El nombre de usuario es obligatorio.'})
        if not password:
            return render(request, 'login.html', {'error': 'La contraseña es obligatoria.'})

        # OWASP A07: bloqueo temporal tras varios intentos fallidos.
        if seguridad_acceso.login_bloqueado(request, username):
            return render(request, 'login.html', {
                'error': seguridad_acceso.mensaje_bloqueo(),
                'username_val': username,
            }, status=429)

        user_obj = Usuario.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=username)
        ).first()

        user = authenticate(
            request,
            username=user_obj.username if user_obj else username,
            password=password,
        )

        # El estado de la cuenta solo se informa si la contraseña es correcta,
        # para no revelar qué cuentas existen (enumeración de usuarios).
        if user is None and user_obj and not user_obj.is_active and user_obj.check_password(password):
            seguridad_acceso.registrar_login_exitoso(request, username)
            return render(request, 'login.html', {'error': 'Usuario bloqueado o inactivo.'})

        if user is None:
            seguridad_acceso.registrar_login_fallido(request, username)

        if user is not None:
            seguridad_acceso.registrar_login_exitoso(request, username)
            if user.must_change_password and (
                not user.initial_login_expires_at
                or user.initial_login_expires_at <= timezone.now()
            ):
                user.is_active = False
                user.save(update_fields=['is_active'])
                return render(request, 'login.html', {
                    'error': (
                        'El plazo de 10 minutos para activar esta cuenta venció. '
                        'Contacte al administrador para renovar el acceso.'
                    ),
                    'username_val': username,
                })

            if user.must_change_password:
                request.session['pending_initial_password_user_id'] = user.pk
                request.session.set_expiry(600)
                return redirect('web_initial_password')

            # Flujo 2FA
            if user.requires_2fa:
                request.session['pending_2fa_user_id'] = user.pk
                request.session.set_expiry(300)
                if code_2fa:
                    if seguridad_acceso.two_factor_bloqueado(user):
                        request.session.pop('pending_2fa_user_id', None)
                        return render(request, 'login.html', {
                            'error': seguridad_acceso.mensaje_bloqueo(),
                        }, status=429)
                    if es_codigo_2fa_valido(user, code_2fa):
                        seguridad_acceso.registrar_2fa_exitoso(user)
                        request.session.pop('pending_2fa_user_id', None)
                        login(request, user)
                        RegistroAuditoria.objects.create(
                            usuario=user, accion='INICIO_SESION_2FA', entidad='Usuario',
                            identificador_registro=str(user.id),
                            ip_origen=obtener_ip_cliente(request) or None
                        )
                        messages.success(request, f"¡Sesión iniciada con éxito! Autenticación 2FA verificada para {user.get_full_name() or user.username}.")
                        return redirect('web_dashboard')
                    else:
                        seguridad_acceso.registrar_2fa_fallido(request, user)
                        return render(request, 'login.html', {
                            'error': 'Código 2FA incorrecto. Ingrese el código temporal de 6 dígitos generado por su dispositivo.',
                            'show_2fa_checked': True,
                            'username_val': username
                        })
                else:
                    return render(request, 'login.html', {
                        'show_2fa': True,
                        'user_id': user.id
                    })

            login(request, user)
            RegistroAuditoria.objects.create(
                usuario=user, accion='INICIO_SESION', entidad='Usuario',
                identificador_registro=str(user.id),
                valor_nuevo={'username': user.username},
                ip_origen=obtener_ip_cliente(request) or None
            )
            return redirect('web_dashboard')
        else:
            return render(request, 'login.html', {
                'error': 'Credenciales inválidas.',
                'username_val': username,
                'show_2fa_checked': False
            })

    return render(request, 'login.html')


@login_not_required
def web_initial_password(request):
    user_id = request.session.get('pending_initial_password_user_id')
    if not user_id:
        messages.error(request, 'Inicia sesión con tu correo y RUN para continuar.')
        return redirect('web_login')

    user = Usuario.objects.filter(pk=user_id, is_active=True).first()
    if not user or not user.must_change_password:
        request.session.pop('pending_initial_password_user_id', None)
        return redirect('web_login')

    if request.method == 'POST':
        form = InitialPasswordForm(request.POST, user=user)
        if form.is_valid():
            user.set_password(form.cleaned_data['new_password1'])
            user.must_change_password = False
            user.initial_login_expires_at = None
            user.save(update_fields=[
                'password',
                'must_change_password',
                'initial_login_expires_at',
            ])
            request.session.pop('pending_initial_password_user_id', None)

            if user.requires_2fa:
                request.session['pending_2fa_user_id'] = user.pk
                request.session.set_expiry(300)
                messages.success(
                    request,
                    'Contraseña guardada. Completa la verificación de dos factores.',
                )
                return render(request, 'login.html', {
                    'show_2fa': True,
                    'user_id': user.pk,
                })

            login(request, user)
            RegistroAuditoria.objects.create(
                usuario=user,
                accion='INICIO_SESION',
                entidad='Usuario',
                identificador_registro=str(user.pk),
                valor_nuevo={'username': user.username},
                ip_origen=obtener_ip_cliente(request) or None,
            )
            messages.success(request, 'Contraseña guardada. Sesión iniciada correctamente.')
            return redirect('web_dashboard')
    else:
        form = InitialPasswordForm(user=user)

    return render(request, 'initial_password.html', {
        'form': form,
        'email': user.email,
    })


@login_not_required
def web_verificar_2fa(request):
    if request.method == 'POST':
        user_id = request.session.get('pending_2fa_user_id')
        code = request.POST.get('code', '').strip()
        if not user_id:
            return redirect('web_login')
        try:
            user = Usuario.objects.get(id=user_id, is_active=True, requires_2fa=True)
            if seguridad_acceso.two_factor_bloqueado(user):
                request.session.pop('pending_2fa_user_id', None)
                return render(request, 'login.html', {
                    'error': seguridad_acceso.mensaje_bloqueo(),
                }, status=429)
            if es_codigo_2fa_valido(user, code):
                seguridad_acceso.registrar_2fa_exitoso(user)
                request.session.pop('pending_2fa_user_id', None)
                login(request, user)
                RegistroAuditoria.objects.create(
                    usuario=user, accion='VERIFICACION_2FA', entidad='Usuario',
                    identificador_registro=str(user.id),
                    ip_origen=obtener_ip_cliente(request) or None
                )
                messages.success(request, f"¡Autenticación 2FA confirmada para {user.get_full_name() or user.username}!")
                return redirect('web_dashboard')
            else:
                seguridad_acceso.registrar_2fa_fallido(request, user)
                return render(request, 'login.html', {
                    'show_2fa': True,
                    'user_id': user.pk,
                    'error': 'Código 2FA incorrecto. Verifique el token de 6 dígitos en su dispositivo e intente nuevamente.'
                })
        except Usuario.DoesNotExist:
            request.session.pop('pending_2fa_user_id', None)
            return redirect('web_login')
    return redirect('web_login')

@require_POST
def web_logout(request):
    logout(request)
    return redirect('web_login')

@never_cache
def web_dashboard(request):
    if request.user.es_administrativo:
        actividades = Actividad.objects.filter(funcionario=request.user)
        evidencias_pendientes = Evidencia.objects.filter(
            actividad__funcionario=request.user,
            estado=Evidencia.Estado.PENDIENTE,
        ).count()
        return render(request, 'dashboard_administrativo.html', {
            'periodo': get_periodo_activo(),
            'actividades_count': actividades.count(),
            'evidencias_pendientes': evidencias_pendientes,
            'compromisos_count': CompromisoAgenda.objects.filter(
                responsable=request.user,
            ).count(),
            'vencimientos': proximos_vencimientos(request.user),
        })

    periodo, periodo_configurado = get_periodo_trimestre_actual()
    periodo_nombre_actual = get_nombre_trimestre_actual()
    delegaciones = Delegacion.objects.filter(activo=True)
    comunal = _resumen_comunal_visible(request.user, periodo)

    if request.user.esta_limitado_a_delegacion:
        delegaciones = delegaciones.filter(pk=request.user.delegacion_id) if request.user.delegacion_id else delegaciones.none()
        delegacion_seleccionada = request.user.delegacion_id
    else:
        delegacion_seleccionada = None
        del_id_param = request.GET.get('delegacion_id')
        if del_id_param:
            try:
                candidate_id = int(del_id_param)
            except ValueError:
                candidate_id = None
            if candidate_id and delegaciones.filter(pk=candidate_id).exists():
                delegacion_seleccionada = candidate_id

    if delegacion_seleccionada and comunal:
        delegaciones_filtradas = [
            d for d in comunal['delegaciones']
            if d['delegacion_id'] == delegacion_seleccionada
        ]
    else:
        delegaciones_filtradas = comunal['delegaciones'] if comunal else []

    total_funcionarios = sum(d['total_funcionarios'] for d in delegaciones_filtradas)
    total_pendientes_check = sum(d['evidencias_pendientes_check'] for d in delegaciones_filtradas)
    total_vencidos = sum(d['compromisos']['vencidos'] for d in delegaciones_filtradas)
    
    if delegaciones_filtradas:
        promedio_ponderado = round(sum(d['promedio_ponderado_pct'] for d in delegaciones_filtradas) / len(delegaciones_filtradas), 1)
    else:
        promedio_ponderado = 0.0

    meta_esp = comunal['meta_esperada_pct'] if comunal else 0.0
    semaforo_promedio = determinar_color_semaforo(
        promedio_ponderado,
        meta_esp,
        periodo.umbral_semaforo_verde if periodo else 100.0,
        periodo.umbral_semaforo_ambar if periodo else 60.0,
    )
    
    funcionarios_lista = []
    for d in delegaciones_filtradas:
        funcionarios_lista.extend(d['funcionarios'])

    context = {
        'comunal': comunal,
        'periodo': periodo,
        'periodo_configurado': periodo_configurado,
        'periodo_nombre_actual': periodo_nombre_actual,
        'delegaciones': delegaciones,
        'total_delegaciones': delegaciones.count(),
        'delegacion_seleccionada': delegacion_seleccionada,
        'total_funcionarios': total_funcionarios,
        'total_pendientes_check': total_pendientes_check,
        'total_vencidos': total_vencidos,
        'promedio_ponderado': promedio_ponderado,
        'semaforo_promedio': semaforo_promedio,
        'funcionarios_lista': funcionarios_lista,
        'resumenes_por_cargo': _agrupar_funcionarios_por_cargo(funcionarios_lista),
        'delegaciones_filtradas': delegaciones_filtradas,
        'delegacion_actual': request.user.delegacion if request.user.esta_limitado_a_delegacion else None,
    }

    if request.user.esta_limitado_a_delegacion:
        # Panel Territorial: la delegación sale siempre del usuario, nunca de la URL.
        template = 'dashboard_delegado.html'
        if periodo and request.user.delegacion_id:
            context['territorial'] = panel_territorial(periodo, request.user.delegacion)
    else:
        template = 'dashboard_admin.html' if request.user.es_admin() else 'dashboard_supervisor.html'
        if periodo:
            context['comparacion_territorial'] = comparacion_territorial(
                periodo, list(Delegacion.objects.filter(activo=True).order_by('nombre')),
            )
    return render(request, template, context)

def web_semaforo(request):
    if request.user.es_administrativo:
        messages.warning(request, 'El semáforo territorial está disponible para supervisores y administradores.')
        return redirect('web_dashboard')

    periodo, periodo_configurado = get_periodo_trimestre_actual()
    delegaciones = Delegacion.objects.filter(activo=True)

    comunal = _resumen_comunal_visible(request.user, periodo)
    if request.user.esta_limitado_a_delegacion:
        delegaciones = delegaciones.filter(pk=request.user.delegacion_id) if request.user.delegacion_id else delegaciones.none()
        delegacion_seleccionada = request.user.delegacion_id
    else:
        delegacion_seleccionada = None
        del_id_param = request.GET.get('delegacion_id')
        if del_id_param:
            try:
                candidate_id = int(del_id_param)
            except ValueError:
                candidate_id = None
            if candidate_id and delegaciones.filter(pk=candidate_id).exists():
                delegacion_seleccionada = candidate_id

    if delegacion_seleccionada and comunal:
        delegaciones_filtradas = [
            d for d in comunal['delegaciones']
            if d['delegacion_id'] == delegacion_seleccionada
        ]
    else:
        delegaciones_filtradas = comunal['delegaciones'] if comunal else []

    meta_esperada_pct = comunal['meta_esperada_pct'] if comunal else 0.0
    umbral_verde = periodo.umbral_semaforo_verde if periodo else 100.0
    umbral_ambar = periodo.umbral_semaforo_ambar if periodo else 60.0
    umbral_60_dia = comunal['info_tiempo']['umbral_60_dia'] if comunal else 0.0

    # CU-15 (HU-19, RF-030): el umbral de días sin registros es el de la regla de
    # inactividad vigente (CU-07), el mismo que usan las alertas operativas.
    regla_inactividad = reglas_vigentes()['INACTIVIDAD']
    umbral_inactividad = int(
        regla_inactividad.umbral
        if regla_inactividad.umbral is not None
        else REGLAS_POR_DEFECTO['INACTIVIDAD']['umbral']
    )
    hoy = timezone.localdate()

    funcionarios_semaforo = []
    for d in delegaciones_filtradas:
        for f in d['funcionarios']:
            avance = f['total_ponderado_pct']
            color = determinar_color_semaforo(avance, meta_esperada_pct, umbral_verde, umbral_ambar)
            dias_sin_registro = f.get('dias_sin_ingreso')
            if dias_sin_registro is None and periodo:
                # Sin registros en el período: se cuenta desde su inicio (igual que CU-37).
                dias_sin_registro = max((min(hoy, periodo.fecha_termino) - periodo.fecha_inicio).days, 0)
            funcionarios_semaforo.append({
                'id': f['funcionario_id'],
                'area': f.get('cargo') or 'Gestión Territorial',
                'responsable': f.get('nombre_completo') or f.get('username'),
                'delegacion': d.get('nombre') or f.get('delegacion_codigo', 'Central'),
                'objetivo_al_dia': meta_esperada_pct,
                'umbral_60': umbral_60_dia,
                'avance_real': avance,
                # CU-14 (HU-17): brecha en puntos porcentuales frente al esperado al día.
                'brecha_pp': round(avance - meta_esperada_pct, 2),
                'color_semaforo': color,
                'dias_sin_ingreso': f.get('dias_sin_ingreso'),
                'ultimo_registro': f.get('ultima_actividad_fecha'),
                'dias_sin_registro': dias_sin_registro,
                'total_registros': f.get('total_actividades', 0),
                'promedio_diario': f.get('promedio_diario', 0),
                'inactivo': bool(
                    regla_inactividad.activa
                    and dias_sin_registro is not None
                    and dias_sin_registro >= umbral_inactividad
                ),
            })

    funcionarios_semaforo.sort(key=lambda x: x['avance_real'], reverse=True)
    total_inactivos = sum(1 for f in funcionarios_semaforo if f['inactivo'])

    if funcionarios_semaforo:
        promedio_avance = round(sum(f['avance_real'] for f in funcionarios_semaforo) / len(funcionarios_semaforo), 2)
        brecha_promedio = round(promedio_avance - meta_esperada_pct, 2)
        color_promedio = determinar_color_semaforo(
            promedio_avance, meta_esperada_pct, umbral_verde, umbral_ambar
        )
    else:
        promedio_avance = 0.0
        brecha_promedio = 0.0
        color_promedio = 'AMBAR'

    return render(request, 'semaforo_al_dia.html', {
        'comunal': comunal,
        'periodo': periodo,
        'periodo_configurado': periodo_configurado,
        'delegaciones': delegaciones,
        'delegacion_seleccionada': delegacion_seleccionada,
        'funcionarios_semaforo': funcionarios_semaforo,
        'meta_esperada_pct': meta_esperada_pct,
        'umbral_60_dia': umbral_60_dia,
        'umbral_verde': umbral_verde,
        'umbral_ambar': umbral_ambar,
        'promedio_avance': promedio_avance,
        'color_promedio': color_promedio,
        'brecha_promedio': brecha_promedio,
        'umbral_inactividad': umbral_inactividad,
        'total_inactivos': total_inactivos,
    })

@login_required(login_url='web_login')
def web_alertas_operativas(request):
    periodos = PeriodoMedicion.objects.order_by('-fecha_inicio', '-pk')
    periodo_id = request.GET.get('periodo')
    if periodo_id:
        periodo = get_object_or_404(periodos, pk=periodo_id)
    else:
        periodo = get_periodo_activo()

    hoy = timezone.localdate()
    alertas = []
    # CU-07: una situación nueva se evalúa con la regla vigente; una alerta ya
    # generada se sigue evaluando con la regla con que se generó, de modo que un
    # cambio de regla no altera las alertas ya generadas.
    reglas = reglas_vigentes()
    # Cada candidata: (clave, tipo, condición(regla) -> bool, constructor() -> dict).
    candidatas = []

    compromisos = CompromisoAgenda.objects.select_related(
        'responsable', 'delegacion',
    ).exclude(
        estado__in=(
            CompromisoAgenda.Estado.REALIZADO,
            CompromisoAgenda.Estado.COMPLETADO,
            'CUMPLIDO',
            'CANCELADO',
        )
    )
    if request.user.es_administrativo:
        compromisos = compromisos.filter(responsable=request.user)
    else:
        compromisos = _filtrar_por_delegacion(compromisos, request.user)
    if periodo:
        compromisos = compromisos.filter(
            fecha_compromiso__range=(periodo.fecha_inicio, periodo.fecha_termino)
        )

    url_tubo = f"{reverse('web_tubo')}?periodo={periodo.pk if periodo else 'todos'}"

    def _alerta_compromiso(compromiso, tipo, nivel, titulo):
        return lambda: {
            'tipo': tipo,
            'nivel': nivel,
            'titulo': titulo,
            'motivo': compromiso.descripcion or 'Compromiso sin descripción.',
            'responsable': compromiso.responsable.get_full_name()
            or compromiso.responsable.username,
            'fecha': compromiso.fecha_compromiso,
            'url': f'{url_tubo}#compromiso-{compromiso.pk}',
        }

    for compromiso in compromisos:
        fecha_compromiso = compromiso.fecha_compromiso
        if fecha_compromiso < hoy:
            candidatas.append((
                f'VENCIMIENTO:compromiso:{compromiso.pk}:{fecha_compromiso.isoformat()}',
                'VENCIMIENTO',
                lambda regla, f=fecha_compromiso: f < hoy - timedelta(days=int(regla.umbral or 0)),
                _alerta_compromiso(
                    compromiso, 'VENCIMIENTO', 'danger', f'Compromiso #{compromiso.pk} vencido',
                ),
            ))
        else:
            candidatas.append((
                f'PLAZO_PROXIMO:compromiso:{compromiso.pk}:{fecha_compromiso.isoformat()}',
                'PLAZO_PROXIMO',
                lambda regla, f=fecha_compromiso: f <= hoy + timedelta(days=int(regla.umbral or 0)),
                _alerta_compromiso(
                    compromiso, 'PLAZO_PRÓXIMO', 'warning',
                    f'Compromiso #{compromiso.pk} próximo a vencer',
                ),
            ))

    evidencias_pendientes = Evidencia.objects.filter(
        estado=Evidencia.Estado.PENDIENTE,
        actividad__anulada=False,
    ).select_related('actividad', 'actividad__funcionario', 'actividad__delegacion')
    if request.user.es_administrativo:
        evidencias_pendientes = evidencias_pendientes.filter(
            actividad__funcionario=request.user,
        )
    else:
        evidencias_pendientes = _filtrar_por_delegacion(
            evidencias_pendientes,
            request.user,
            campo='actividad__delegacion_id',
        )
    if periodo:
        evidencias_pendientes = evidencias_pendientes.filter(
            actividad__fecha_actividad__range=(
                periodo.fecha_inicio,
                periodo.fecha_termino,
            )
        )
    ahora = timezone.now()

    def _alerta_evidencia(evidencia):
        def construir():
            funcionario = evidencia.actividad.funcionario
            return {
                'tipo': 'EVIDENCIA_PENDIENTE',
                'nivel': 'warning',
                'titulo': f'Evidencia {evidencia.codigo_verificador} pendiente de validación',
                'motivo': (
                    evidencia.actividad.actividad_solicitud
                    or evidencia.actividad.solicitud_problema
                    or 'Actividad sin descripción.'
                ),
                'responsable': funcionario.get_full_name() or funcionario.username,
                'fecha': evidencia.fecha_subida,
                'url': (
                    f"{reverse('web_verificacion')}?estado=PENDIENTE"
                    f"#evidencia-{evidencia.pk}"
                ),
            }
        return construir

    for evidencia in evidencias_pendientes:
        candidatas.append((
            f'EVIDENCIA_PENDIENTE:evidencia:{evidencia.pk}',
            'EVIDENCIA_PENDIENTE',
            lambda regla, subida=evidencia.fecha_subida: bool(subida)
            and subida <= ahora - timedelta(days=float(regla.umbral or 0)),
            _alerta_evidencia(evidencia),
        ))

    funcionarios = Usuario.objects.filter(
        is_active=True,
        rol=Usuario.Rol.ADMINISTRATIVO,
        es_delegado=False,
    ).select_related('cargo', 'delegacion')
    if request.user.es_administrativo:
        funcionarios = funcionarios.filter(pk=request.user.pk)
    else:
        funcionarios = _filtrar_por_delegacion(funcionarios, request.user)

    def _rendimiento_bajo(metricas):
        def condicion(regla):
            if regla.umbral is None:
                return metricas['semaforo_global'] == 'ROJO'
            esperado = metricas['meta_esperada_pct'] or 0
            return bool(
                esperado
                and metricas['total_ponderado_pct'] / esperado * 100 < regla.umbral
            )
        return condicion

    if periodo:
        for funcionario in funcionarios:
            metricas = calcular_metricas_funcionario(funcionario, periodo, hoy)
            ultima_actividad = metricas['ultima_actividad']
            url_ficha = f"{reverse('web_personal')}?id={funcionario.pk}&periodo={periodo.pk}"
            candidatas.append((
                f'RENDIMIENTO_BAJO:funcionario:{funcionario.pk}:periodo:{periodo.fecha_inicio.isoformat()}',
                'RENDIMIENTO_BAJO',
                _rendimiento_bajo(metricas),
                lambda m=metricas, u=url_ficha: {
                    'tipo': 'RENDIMIENTO_BAJO',
                    'nivel': 'danger',
                    'titulo': 'Avance bajo el umbral esperado',
                    'motivo': (
                        f"Avance {m['total_ponderado_pct']:.1f}% frente a "
                        f"{m['meta_esperada_pct']:.1f}% esperado."
                    ),
                    'responsable': m['nombre_completo'],
                    'fecha': hoy,
                    'url': u,
                },
            ))
            dias_sin_registro = metricas['dias_sin_ingreso']
            if dias_sin_registro is None:
                dias_sin_registro = (hoy - periodo.fecha_inicio).days
            candidatas.append((
                (
                    f'INACTIVIDAD:funcionario:{funcionario.pk}:periodo:{periodo.fecha_inicio.isoformat()}:'
                    f'{ultima_actividad or "sin-registros"}'
                ),
                'INACTIVIDAD',
                lambda regla, d=dias_sin_registro: d >= (regla.umbral or 0),
                lambda m=metricas, d=dias_sin_registro, ua=ultima_actividad, u=url_ficha: {
                    'tipo': 'INACTIVIDAD',
                    'nivel': 'warning',
                    'titulo': 'Sin actividad registrada recientemente',
                    'motivo': (
                        'No hay actividades registradas en el período.'
                        if not ua
                        else f'{d} días desde el último registro.'
                    ),
                    'responsable': m['nombre_completo'],
                    'fecha': date.fromisoformat(ua[:10]) if ua else periodo.fecha_inicio,
                    'url': u,
                },
            ))

    generadas = reglas_de_alertas_generadas(candidata[0] for candidata in candidatas)
    nuevas = []
    for clave, tipo, condicion, construir in candidatas:
        regla = generadas.get(clave) or reglas[tipo]
        if not regla.activa or not condicion(regla):
            continue
        if clave not in generadas:
            nuevas.append((clave, regla))
        if aplica_a(regla, request.user):
            alertas.append({'clave': clave, **construir()})
    registrar_alertas_generadas(nuevas)

    alertas.sort(
        key=lambda alerta: (alerta['fecha'].isoformat(), alerta['titulo'])
    )
    # CU-37: marca de revisión (la alerta no se elimina; queda visible como revisada).
    revisadas = {
        revision.clave: revision
        for revision in AlertaRevisada.objects.filter(
            clave__in=[alerta['clave'] for alerta in alertas],
        ).select_related('revisada_por')
    }
    for alerta in alertas:
        alerta['revision'] = revisadas.get(alerta['clave'])
    solo_pendientes = request.GET.get('ver') == 'pendientes'
    if solo_pendientes:
        alertas = [alerta for alerta in alertas if not alerta['revision']]
    return render(request, 'alertas_operativas.html', {
        'alertas': alertas,
        'periodos': periodos,
        'periodo_seleccionado': periodo,
        'hoy': hoy,
        'solo_pendientes': solo_pendientes,
    })


@login_required(login_url='web_login')
@require_POST
def web_marcar_alerta_revisada(request):
    """CU-37: marca una alerta como revisada sin eliminar el evento original."""
    clave = (request.POST.get('clave') or '').strip()[:200]
    tipo = clave.split(':', 1)[0]
    if tipo not in REGLAS_POR_DEFECTO:
        messages.error(request, 'La alerta indicada no es válida.')
        return redirect('web_alertas_operativas')
    with transaction.atomic():
        revision, creada = AlertaRevisada.objects.get_or_create(
            clave=clave,
            defaults={
                'tipo': tipo,
                'revisada_por': request.user,
                'comentario': (request.POST.get('comentario') or '').strip()[:500],
            },
        )
        if creada:
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='REVISAR_ALERTA',
                evento='MODIFICACION',
                entidad='AlertaRevisada',
                identificador=str(revision.pk),
                identificador_registro=str(revision.pk),
                valor_anterior=None,
                valor_nuevo={'clave': clave, 'comentario': revision.comentario},
                ip_origen=obtener_ip_cliente(request) or None,
            )
    messages.success(request, 'Alerta marcada como revisada.')
    destino = request.POST.get('volver') or ''
    if not destino.startswith('/alertas/'):
        destino = reverse('web_alertas_operativas')
    return redirect(destino)

@login_required(login_url='web_login')
def web_reportes(request):
    # El delegado accede solo a su delegación: todas las consultas pasan por
    # _filtrar_por_delegacion, y el filtro ?delegacion= se suma a ese límite.
    if not (
        request.user.is_superuser
        or request.user.es_admin()
        or request.user.es_supervisor
        or request.user.es_consulta
        or (request.user.esta_limitado_a_delegacion and request.user.delegacion_id)
    ):
        raise PermissionDenied

    periodos = PeriodoMedicion.objects.order_by('-fecha_inicio', '-pk')
    periodo_id = request.GET.get('periodo')
    periodo = (
        get_object_or_404(periodos, pk=periodo_id)
        if periodo_id else get_periodo_activo()
    )
    delegaciones = _filtrar_por_delegacion(
        Delegacion.objects.filter(activo=True),
        request.user,
        campo='pk',
    )
    funcionarios = Usuario.objects.filter(
        is_active=True,
        rol=Usuario.Rol.ADMINISTRATIVO,
        es_delegado=False,
    )
    funcionarios = _filtrar_por_delegacion(funcionarios, request.user)
    actividades = Actividad.objects.select_related(
        'funcionario', 'delegacion', 'item_medicion',
    ).prefetch_related('evidencias').order_by('-fecha_actividad', '-pk')
    actividades = _filtrar_por_delegacion(actividades, request.user)

    estado = request.GET.get('estado', '').strip()
    if estado == 'ANULADA':
        actividades = actividades.filter(anulada=True)
    elif estado != 'TODAS':
        actividades = actividades.filter(anulada=False)
    fecha_desde_texto = request.GET.get('fecha_desde', '').strip()
    fecha_hasta_texto = request.GET.get('fecha_hasta', '').strip()
    fecha_desde = parse_date(fecha_desde_texto) if fecha_desde_texto else None
    fecha_hasta = parse_date(fecha_hasta_texto) if fecha_hasta_texto else None
    fecha_filtro_invalido = False
    if fecha_desde_texto and not fecha_desde:
        messages.error(request, 'La fecha inicial del filtro no es válida.')
        fecha_filtro_invalido = True
    if fecha_hasta_texto and not fecha_hasta:
        messages.error(request, 'La fecha final del filtro no es válida.')
        fecha_filtro_invalido = True
    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        messages.error(request, 'La fecha inicial no puede ser posterior a la fecha final.')
        fecha_filtro_invalido = True
    if fecha_desde:
        actividades = actividades.filter(fecha_actividad__gte=fecha_desde)
    if fecha_hasta:
        actividades = actividades.filter(fecha_actividad__lte=fecha_hasta)
    if fecha_filtro_invalido:
        actividades = actividades.none()

    if periodo:
        actividades = actividades.filter(
            fecha_actividad__range=(periodo.fecha_inicio, periodo.fecha_termino)
        )
    delegacion_id = request.GET.get('delegacion')
    if delegacion_id:
        actividades = actividades.filter(delegacion_id=delegacion_id)
        funcionarios = funcionarios.filter(delegacion_id=delegacion_id)
    funcionario_id = request.GET.get('funcionario')
    if funcionario_id:
        actividades = actividades.filter(funcionario_id=funcionario_id)
        funcionarios = funcionarios.filter(pk=funcionario_id)
    cargo_id = request.GET.get('cargo')
    if cargo_id:
        actividades = actividades.filter(funcionario__cargo_id=cargo_id)
        funcionarios = funcionarios.filter(cargo_id=cargo_id)
    area_id = request.GET.get('area')
    if area_id:
        actividades = actividades.filter(funcionario__area_entidad_id=area_id)
        funcionarios = funcionarios.filter(area_entidad_id=area_id)
    item_id = request.GET.get('item')
    if item_id:
        actividades = actividades.filter(item_medicion_id=item_id)
    if estado and estado not in ('ANULADA', 'TODAS'):
        actividades = actividades.filter(evidencias__estado=estado).distinct()
    termino = request.GET.get('q', '').strip()
    if termino:
        # CU-39 (HU-10): también se busca por código de registro o código verificador.
        actividades = actividades.filter(
            Q(actividad_solicitud__icontains=termino)
            | Q(accion_ejecutada__icontains=termino)
            | Q(direccion__icontains=termino)
            | Q(codigo_registro__iexact=termino)
            | Q(pk__in=Evidencia.objects.filter(
                codigo_verificador__iexact=termino,
            ).values('actividad_id'))
        )

    if request.GET.get('formato') == 'csv':
        # La marca BOM se escribe una sola vez; con utf-8-sig se repetiría en cada fila.
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="informe_sgr_{timezone.localdate():%Y%m%d}.csv"'
        )
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['Informe SGR', 'Generado', timezone.localtime().isoformat()])
        writer.writerow([
            'Período',
            periodo.nombre if periodo else 'Todos',
            'Filtros',
            f'delegacion={delegacion_id or "todas"}; funcionario={funcionario_id or "todos"}; '
            f'cargo={cargo_id or "todos"}; area={area_id or "todas"}; '
            f'item={item_id or "todos"}; estado={estado or "activos"}; '
            f'fecha_desde={fecha_desde_texto or "sin límite"}; '
            f'fecha_hasta={fecha_hasta_texto or "sin límite"}; '
            # La celda siempre empieza con "delegacion=", por lo que no puede ser una fórmula.
            f'búsqueda={termino or "sin texto"}',
        ])
        writer.writerow([
            'ID', 'Código', 'Fecha', 'Funcionario', 'Delegación', 'Ítem',
            'Actividad/Solicitud', 'Acción', 'Evidencias aprobadas',
        ])
        for actividad in actividades:
            evidencias_aprobadas = sum(
                evidencia.estado == Evidencia.Estado.APROBADA
                for evidencia in actividad.evidencias.all()
            )
            writer.writerow([
                actividad.pk,
                actividad.codigo_registro,
                actividad.fecha_actividad.isoformat(),
                _celda_csv_segura(
                    actividad.funcionario.get_full_name() or actividad.funcionario.username
                ),
                _celda_csv_segura(actividad.delegacion.nombre),
                _celda_csv_segura(
                    actividad.item_medicion.nombre_item if actividad.item_medicion_id else ''
                ),
                _celda_csv_segura(actividad.actividad_solicitud),
                _celda_csv_segura(actividad.accion_ejecutada),
                evidencias_aprobadas,
            ])
        return response

    paginador = Paginator(actividades, 25)
    pagina = paginador.get_page(request.GET.get('page'))
    items = ItemMedicion.objects.filter(activo=True)
    if periodo and periodo.pk:
        items = items.filter(periodo_id=periodo.pk)
    else:
        items = items.none()
    cargos = Cargo.objects.filter(activo=True).order_by('nombre')
    areas = Area.objects.filter(estado=True).order_by('nombre')
    return render(request, 'reportes.html', {
        'periodos': periodos,
        'periodo_seleccionado': periodo,
        'delegaciones': delegaciones,
        'funcionarios': funcionarios,
        'cargos': cargos,
        'areas': areas,
        'items': items.distinct(),
        'pagina': pagina,
        'filtros': {
            'delegacion': delegacion_id or '',
            'funcionario': funcionario_id or '',
            'cargo': cargo_id or '',
            'area': area_id or '',
            'item': item_id or '',
            'estado': estado,
            'fecha_desde': fecha_desde_texto,
            'fecha_hasta': fecha_hasta_texto,
            'q': termino,
        },
        'total_resultados': paginador.count,
        'query_sin_pagina': request.GET.copy(),
    })

@login_required(login_url='web_login')
def web_personal(request):
    periodo_actual, periodo_actual_configurado = get_periodo_trimestre_actual()
    periodos_disponibles = PeriodoMedicion.objects.order_by('-fecha_inicio', '-pk')
    periodo_param = request.GET.get('periodo', '')
    periodo = periodo_actual
    periodo_configurado = periodo_actual_configurado
    periodo_seleccionado = str(periodo_actual.pk) if periodo_actual.pk else 'actual'
    if periodo_param == 'actual':
        periodo_seleccionado = 'actual'
    elif periodo_param:
        periodo_seleccionado = periodo_param
        periodo_elegido = (
            periodos_disponibles.filter(pk=int(periodo_param)).first()
            if periodo_param.isdecimal()
            else None
        )
        if periodo_elegido:
            periodo = periodo_elegido
            periodo_configurado = True
        else:
            messages.error(request, 'El período seleccionado no existe o no está disponible.')
            periodo_seleccionado = str(periodo_actual.pk) if periodo_actual.pk else 'actual'

    todos_funcionarios = Usuario.objects.filter(
        rol=Usuario.Rol.ADMINISTRATIVO, es_delegado=False, is_active=True
    ).select_related('delegacion', 'cargo')
    todos_funcionarios = _filtrar_por_delegacion(
        todos_funcionarios,
        request.user,
    )
    
    # Si el usuario logueado es Funcionario, solo puede ver su propia ficha personal
    if request.user.is_authenticated and request.user.es_administrativo:
        funcionario = request.user
    else:
        id_param = (request.GET.get('id') or '').strip()
        funcionario = None
        if id_param:
            if id_param.isdecimal():
                funcionario = todos_funcionarios.filter(id=int(id_param)).first()
            if not funcionario:
                messages.error(request, 'El funcionario solicitado no existe o no está disponible.')
        elif todos_funcionarios.exists():
            funcionario = todos_funcionarios.first()

    metricas = None
    actividades = []
    pagina_actividades = None
    items_funcionario = []
    item_origen = None
    if funcionario:
        actividades_query = Actividad.objects.filter(
            funcionario=funcionario,
            fecha_actividad__gte=periodo.fecha_inicio,
            fecha_actividad__lte=periodo.fecha_termino,
        ).select_related(
            'item_medicion', 'delegacion', 'compromiso_agenda'
        ).annotate(
            tiene_evidencia_aprobada=Exists(
                Evidencia.objects.filter(
                    actividad_id=OuterRef('pk'),
                    estado=Evidencia.Estado.APROBADA,
                )
            )
        ).prefetch_related(
            'evidencias',
            'compromisos',
            'comentarios__autor',
        ).order_by('-fecha_actividad', '-fecha_registro')
        if periodo:
            metricas = calcular_metricas_funcionario(funcionario, periodo)
            if periodo.pk and funcionario.cargo:
                items_funcionario = list(ItemMedicion.objects.filter(periodo=periodo, cargo=funcionario.cargo, activo=True))

        item_param = request.GET.get('item', '')
        if item_param:
            if item_param.isdecimal():
                item_origen = ItemMedicion.objects.filter(
                    pk=int(item_param),
                    periodo=periodo,
                    cargo=funcionario.cargo,
                    activo=True,
                ).first()
            if item_origen:
                actividades_query = actividades_query.filter(item_medicion=item_origen)
            else:
                messages.error(request, 'El ítem de origen no pertenece al período y funcionario seleccionados.')
        pagina_actividades = Paginator(actividades_query, 50).get_page(
            request.GET.get('pagina')
        )
        actividades = list(pagina_actividades.object_list)
        # RF-028: un compromiso puede vincularse por Actividad.compromiso_agenda
        # o por CompromisoAgenda.actividad; se muestran ambos sin duplicar.
        for actividad in actividades:
            vinculados = {}
            if actividad.compromiso_agenda_id:
                vinculados[actividad.compromiso_agenda_id] = actividad.compromiso_agenda
            for compromiso in actividad.compromisos.all():
                vinculados.setdefault(compromiso.pk, compromiso)
            actividad.compromisos_vinculados = sorted(
                vinculados.values(),
                key=lambda compromiso: compromiso.pk,
            )

    areas_apoyo = [
        'Operaciones y Territorio',
        'Alumbrado Público',
        'Aseo y Ornato',
        'DIDECO / Social',
        'Tránsito y Transporte',
        'Seguridad Ciudadana',
        'Medio Ambiente',
        'SECPLAN / Proyectos'
    ]

    return render(request, 'dashboard_personal.html', {
        'funcionario': funcionario,
        'puede_anular_actividades': puede_anular_actividades(request.user),
        'todos_funcionarios': todos_funcionarios,
        'periodos_disponibles': periodos_disponibles,
        'periodo_seleccionado': periodo_seleccionado,
        'metricas': metricas,
        'actividades': actividades,
        'pagina_actividades': pagina_actividades,
        'item_origen': item_origen,
        'items_funcionario': items_funcionario,
        'areas_apoyo': areas_apoyo,
        'territorios': [
            'Sector Centro', 'El Milagro', 'La Pampa', 'Las Compañías',
            'La Antena', 'Avenida del Mar', 'Sector Rural', 'Otro',
        ],
        'periodo': periodo,
        'periodo_configurado': periodo_configurado,
        'puede_comentar_actividades': (
            request.user.es_admin()
            or request.user.es_supervisor
            or bool(funcionario and funcionario.pk == request.user.pk)
        ),
        'nombre_periodo_panel': (
            get_nombre_trimestre_actual()
            if periodo.pk == periodo_actual.pk
            else periodo.nombre
        ),
        'metas_configuradas': bool(items_funcionario),
        'hoy': timezone.localdate(),
    })

def web_registrar_solicitante(request):
    if not request.user.is_authenticated:
        messages.error(request, 'Debe iniciar sesión para registrar solicitantes.')
        return redirect('web_login')

    form = SolicitanteForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                solicitante = form.save()
        except IntegrityError:
            if Solicitante.objects.filter(run=form.cleaned_data['run']).exists():
                form.add_error('run', 'Ya existe un solicitante registrado con ese RUN.')
            else:
                raise
        else:
            messages.success(request, 'Solicitante registrado correctamente.')
            return redirect(f"{reverse('web_registrar_actividad_diaria')}?solicitante_id={solicitante.id}")

    return render(request, 'registrar_solicitante.html', {
        'form': form,
        'form_data': request.POST if request.method == 'POST' else {},
        'tipo_solicitante_choices': Solicitante.TIPO_SOLICITANTE_CHOICES,
    })

def web_mi_ficha(request):
    if not request.user.is_authenticated:
        messages.error(request, 'Inicia sesión para editar tu ficha personal.')
        return redirect('web_login')

    if request.method == 'POST':
        form = MiFichaPersonalForm(request.POST, instance=request.user)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Tus datos personales se actualizaron correctamente.')
            return redirect('web_mi_ficha')
    else:
        form = MiFichaPersonalForm(instance=request.user, initial={
            'email_confirmacion': request.user.email,
        })

    return render(request, 'mi_ficha.html', {'form': form})

def _actividad_error(request, funcionario_id, error_code, field_errors=None):
    campos_formulario = (
        'fecha_actividad', 'item_medicion_id', 'catalogo_servicio_id', 'solicitante_id',
        'contacto_telefono',
        'direccion', 'solicitud_problema',
        'accion_ejecutada', 'compromiso_fecha',
        'compromiso_territorio', 'compromiso_area_apoyo', 'compromiso_descripcion',
        'latitud', 'longitud', 'crear_compromiso',
    )
    request.session['activity_form_data'] = {
        field: request.POST.get(field, '').strip()
        for field in campos_formulario
    }
    request.session['activity_field_errors'] = field_errors or []
    return redirect(f"{reverse('web_registrar_actividad_diaria')}?funcionario_id={funcionario_id}&activity_error={error_code}")

def _fecha_en_periodo(fecha, periodo):
    return bool(periodo and periodo.fecha_inicio <= fecha <= periodo.fecha_termino)

def _validar_fotos(fotos):
    for foto in fotos:
        validar_archivo_evidencia(foto)

def _guardar_fotos(actividad, fotos, subido_por):
    _validar_fotos(fotos)
    evidencias = []
    for foto in fotos:
        evidencias.append(Evidencia.objects.create(
            actividad=actividad,
            archivo_foto=foto,
            subido_por=subido_por,
            metadatos=metadatos_archivo_evidencia(foto, subido_por),
            estado=Evidencia.Estado.PENDIENTE,
            fecha_evidencia=timezone.localdate(),
        ))
    return evidencias


def _recalcular_metas_evidencia(evidencia):
    return recalcular_indicadores_actividad(evidencia.actividad)

def _funcionarios_disponibles_actividad(user):
    funcionarios = Usuario.objects.filter(
        rol=Usuario.Rol.ADMINISTRATIVO,
        es_delegado=False,
        is_active=True,
    )
    return _filtrar_por_delegacion(funcionarios, user)


def _items_medicion_funcionario(funcionario, periodo):
    if not funcionario or not periodo or not periodo.pk or not funcionario.cargo_id:
        return ItemMedicion.objects.none()
    return ItemMedicion.objects.filter(
        periodo=periodo,
        cargo_id=funcionario.cargo_id,
        activo=True,
    ).order_by('pk')


def _tipos_actividad_funcionario(funcionario):
    tipos = TipoActividad.objects.filter(
        activo=True,
        area__estado=True,
    )
    if funcionario and funcionario.area_entidad_id:
        tipos = tipos.filter(area_id=funcionario.area_entidad_id)
    return tipos.select_related('area').order_by('nombre')


def _servicios_funcionario(funcionario):
    if not funcionario or not funcionario.area_entidad_id:
        return CatalogoServicio.objects.none()
    return CatalogoServicio.objects.filter(
        activo=True,
        area_ref_id=funcionario.area_entidad_id,
    ).order_by('nombre')


@require_GET
def web_items_medicion_funcionario(request):
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Debes iniciar sesión para consultar los ítems.'},
            status=401,
        )

    funcionario_id = request.GET.get('funcionario_id', '')
    if request.user.es_administrativo:
        if funcionario_id and funcionario_id != str(request.user.pk):
            return JsonResponse(
                {'error': 'Solo puedes consultar tus propios ítems.'},
                status=403,
            )
        funcionario = request.user
    elif not funcionario_id:
        funcionario = None
    elif not funcionario_id.isdecimal():
        return JsonResponse(
            {'error': 'El funcionario seleccionado no es válido.'},
            status=400,
        )
    else:
        funcionario = _funcionarios_disponibles_actividad(
            request.user
        ).filter(pk=int(funcionario_id)).first()
        if not funcionario:
            return JsonResponse(
                {'error': 'El funcionario seleccionado no está disponible.'},
                status=404,
            )

    periodo = get_periodo_activo()
    items = list(_items_medicion_funcionario(funcionario, periodo))
    tipos_actividad = list(_tipos_actividad_funcionario(funcionario))
    servicios = list(_servicios_funcionario(funcionario))

    return JsonResponse({
        'items': [
            {
                'id': item.pk,
                'label': (
                    f'{item.nombre_item} (Meta: {item.meta_cuantitativa}, '
                    f'ponderador: {item.ponderador}%)'
                ),
            }
            for item in items
        ],
        'required': bool(items),
        'tipos_actividad': [
            {
                'id': tipo.pk,
                'label': tipo.nombre,
            }
            for tipo in tipos_actividad
        ],
        'tipo_actividad_required': bool(tipos_actividad),
        'servicios': [
            {
                'id': servicio.pk,
                'label': f'{servicio.codigo} — {servicio.nombre} ({servicio.area_ref.nombre})',
            }
            for servicio in servicios
        ],
    })


def web_registrar_actividad_personal(request):
    if not request.user.is_authenticated:
        messages.error(request, "Debe iniciar sesión para registrar actividades.")
        return redirect('web_login')

    periodo = get_periodo_activo()
    funcionarios_disponibles = _funcionarios_disponibles_actividad(
        request.user,
    )
    requester_queryset = Solicitante.objects.all()
    if hasattr(Solicitante, 'estado'):
        requester_queryset = requester_queryset.filter(estado=True)
    def render_activity_form(
        form,
        funcionario=None,
        form_data=None,
        error_code='',
        field_errors=None,
    ):
        selected_requester = None
        if form_data:
            requester_id = form_data.get('solicitante_id', '')
        else:
            requester_id = request.GET.get('solicitante_id', '')
        if str(requester_id).isdigit():
            selected_requester = requester_queryset.filter(pk=int(requester_id)).first()
        contacto_telefono_inicial = (
            form_data.get('contacto_telefono', '')
            if form_data is not None and 'contacto_telefono' in form_data
            else selected_requester.telefono if selected_requester else ''
        )

        item_queryset = _items_medicion_funcionario(funcionario, periodo)
        tipo_actividad_queryset = _tipos_actividad_funcionario(funcionario)

        form.fields['item_medicion_id'].queryset = item_queryset
        form.fields['tipo_actividad_id'].queryset = tipo_actividad_queryset
        form.fields['tipo_actividad_id'].required = tipo_actividad_queryset.exists()
        form.fields['catalogo_servicio_id'].queryset = _servicios_funcionario(
            funcionario,
        )
        return render(request, 'registrar_actividad.html', {
            'form': form,
            'funcionario': funcionario,
            'funcionarios': funcionarios_disponibles.order_by('last_name', 'first_name'),
            'items_funcionario': item_queryset,
            'tipos_actividad': tipo_actividad_queryset,
            'solicitantes': requester_queryset.order_by(
                'nombre', 'apellido_paterno', 'apellido_materno'
            ),
            'catalogos_servicios': _servicios_funcionario(funcionario),
            'selected_requester': selected_requester,
            'contacto_telefono_inicial': contacto_telefono_inicial,
            'activity_form_data': form_data or {},
            'activity_field_errors': field_errors or [],
            'activity_error': {
                'fecha_futura': 'La fecha de la actividad no puede ser posterior a hoy.',
                'fecha_invalida': 'La fecha de la actividad no es válida.',
                'fecha_periodo': 'La fecha de actividad debe pertenecer al período del ítem seleccionado.',
                'fecha_compromiso': 'La fecha del compromiso debe pertenecer al período activo y no ser anterior a la actividad.',
                'solicitante': 'Selecciona un solicitante registrado.',
                'telefono': 'Ingresa un teléfono de contacto chileno válido.',
                'item_invalido': 'Selecciona un ítem válido para el período y cargo.',
                'tipo_actividad': 'Selecciona un tipo de actividad válido para el área.',
                'ubicacion_invalida': 'La ubicación debe incluir coordenadas válidas.',
                'compromiso_datos': 'Completa datos válidos para crear el compromiso opcional.',
                'datos_invalidos': 'Revisa los datos del formulario.',
            }.get(error_code, ''),
            'periodo': periodo,
            'hoy': timezone.localdate(),
            'territorios': ACTIVIDAD_TERRITORIOS,
            'areas_apoyo': ACTIVIDAD_AREAS_APOYO,
        })

    funcionario_id = request.POST.get('funcionario_id', '') if request.method == 'POST' else request.GET.get('funcionario_id', '')
    if request.user.es_administrativo:
        funcionario = request.user
    elif str(funcionario_id).isdigit():
        funcionario = funcionarios_disponibles.filter(pk=int(funcionario_id)).first()
    else:
        funcionario = funcionarios_disponibles.first()

    item_queryset = _items_medicion_funcionario(funcionario, periodo)

    if request.method == 'GET':
        activity_error = request.GET.get('activity_error', '')
        form_data = request.session.pop('activity_form_data', {}) if activity_error else {}
        field_errors = request.session.pop('activity_field_errors', []) if activity_error else []
        form = ActividadTerrenoForm(
            initial={
                'funcionario_id': funcionario.pk if funcionario else '',
                'fecha_actividad': timezone.localdate(),
            },
            funcionario_queryset=funcionarios_disponibles,
            item_queryset=item_queryset,
            tipo_actividad_queryset=_tipos_actividad_funcionario(funcionario),
            catalogo_queryset=_servicios_funcionario(funcionario),
            periodo=periodo,
        )
        return render_activity_form(
            form,
            funcionario,
            form_data,
            activity_error,
            field_errors,
        )

    form = ActividadTerrenoForm(
        request.POST,
        request.FILES,
        funcionario_queryset=funcionarios_disponibles,
        item_queryset=item_queryset,
        tipo_actividad_queryset=_tipos_actividad_funcionario(funcionario),
        catalogo_queryset=_servicios_funcionario(funcionario),
        periodo=periodo,
    )
    form.fields['funcionario_id'].required = not request.user.es_administrativo
    if not form.is_valid():
        if funcionario:
            invalid_fields = set(form.errors.keys())
            field_errors = [
                {
                    'field': form.fields[field].label if field in form.fields else field,
                    'messages': [str(error) for error in errors],
                }
                for field, errors in form.errors.items()
            ]
            error_code = 'datos_invalidos'
            if 'fecha_actividad' in invalid_fields:
                date_errors = str(form.errors['fecha_actividad'])
                if 'posterior a hoy' in date_errors:
                    error_code = 'fecha_futura'
                elif 'período' in date_errors:
                    error_code = 'fecha_periodo'
                else:
                    error_code = 'fecha_invalida'
            elif 'solicitante_id' in invalid_fields:
                error_code = 'solicitante'
            elif 'contacto_telefono' in invalid_fields:
                error_code = 'telefono'
            elif 'item_medicion_id' in invalid_fields:
                error_code = 'item_invalido'
            elif 'tipo_actividad_id' in invalid_fields:
                error_code = 'tipo_actividad'
            elif invalid_fields.intersection({'latitud', 'longitud'}):
                error_code = 'ubicacion_invalida'
            elif 'compromiso_fecha' in invalid_fields:
                error_code = 'fecha_compromiso'
            elif invalid_fields.intersection({
                'compromiso_fecha',
                'compromiso_territorio',
                'compromiso_area_apoyo',
                'compromiso_descripcion',
            }):
                error_code = 'compromiso_datos'
            messages.error(request, 'Revisa los datos del formulario antes de continuar.')
            return _actividad_error(
                request,
                funcionario.pk,
                error_code,
                field_errors,
            )
        messages.error(request, 'Selecciona un funcionario responsable válido.')
        return redirect('web_registrar_actividad_diaria')

    data = form.cleaned_data
    funcionario = request.user if request.user.es_administrativo else data['funcionario_id']
    if request.user.es_administrativo and data['funcionario_id'] != request.user:
        raise PermissionDenied

    delegacion = funcionario.delegacion or Delegacion.objects.first()
    if not delegacion:
        messages.error(request, 'El funcionario no tiene una delegación asignada.')
        return redirect(f"{reverse('web_personal')}?id={funcionario.pk}")

    solicitante = data['solicitante_id']
    item_medicion = data['item_medicion_id']
    fotos = data['fotos']
    compromiso = None
    try:
        with transaction.atomic():
            actividad = Actividad.objects.create(
                funcionario=funcionario,
                delegacion=delegacion,
                solicitante=solicitante,
                item_medicion=item_medicion,
                tipo_actividad=data['tipo_actividad_id'],
                catalogo_servicio=data['catalogo_servicio_id'],
                fecha_actividad=data['fecha_actividad'],
                contacto_nombre=str(solicitante),
                contacto_telefono=data['contacto_telefono'],
                solicitud_problema=data['solicitud_problema'].strip(),
                actividad_solicitud=data['solicitud_problema'].strip(),
                accion=data['accion_ejecutada'].strip(),
                accion_ejecutada=data['accion_ejecutada'].strip(),
                direccion=data['direccion'].strip(),
                latitud=data['latitud'],
                longitud=data['longitud'],
            )
            evidencias = _guardar_fotos(actividad, fotos, request.user) if fotos else []

            if data['crear_compromiso']:
                compromiso = CompromisoAgenda.objects.create(
                    solicitante=str(solicitante),
                    origen='EXT',
                    territorio=data['compromiso_territorio'],
                    delegacion=delegacion,
                    responsable=funcionario,
                    fecha_compromiso=data['compromiso_fecha'],
                    fecha_comprometida=data['compromiso_fecha'],
                    area_apoyo=data['compromiso_area_apoyo'],
                    descripcion=data['compromiso_descripcion'].strip(),
                    observacion='Compromiso generado desde el registro de actividad.',
                    observaciones='Compromiso generado desde el registro de actividad.',
                    estado=CompromisoAgenda.Estado.EN_PROCESO,
                    actividad=actividad,
                    creado_por=request.user,
                )
                actividad.compromiso_agenda = compromiso
                actividad.save(update_fields=['compromiso_agenda'])

            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='REGISTRO_TODO_EN_UNO',
                entidad='Actividad',
                identificador=str(actividad.pk),
                identificador_registro=str(actividad.pk),
                valor_nuevo={
                    'actividad_id': actividad.pk,
                    'funcionario': funcionario.username,
                    'evidencias': [e.codigo_verificador for e in evidencias],
                    'compromiso_id': compromiso.pk if compromiso else None,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        validation_errors = (
            exc.error_dict.items()
            if hasattr(exc, 'error_dict')
            else [('__all__', exc.error_list)]
        )
        field_labels = {
            'item_medicion': 'Ítem de medición',
            'tipo_actividad': 'Tipo de actividad',
        }
        field_errors = [
            {
                'field': (
                    form.fields[field].label
                    if field in form.fields
                    else field_labels.get(field, field.replace('_', ' ').capitalize())
                ),
                'messages': [str(error) for error in errors],
            }
            for field, errors in validation_errors
        ]
        return _actividad_error(
            request,
            funcionario.pk,
            'datos_invalidos',
            field_errors,
        )

    msg_exito = (
        f"Actividad #{actividad.pk} registrada correctamente. "
        f"Código: {actividad.codigo_registro}."
    )
    if evidencias:
        msg_exito += f" Se adjuntaron {len(evidencias)} evidencia(s) para revisión."
    if compromiso:
        msg_exito += f" Se vinculó el compromiso #{compromiso.pk} al Tubo de Trabajo."
    messages.success(request, msg_exito)
    return redirect(f"{reverse('web_personal')}?id={funcionario.pk}")


def puede_anular_actividades(user):
    """CU-26: anula el Delegado (en su delegación) y, por herencia, Coordinador y Administrador."""
    if user.es_consulta:
        return False
    return bool(
        user.is_superuser
        or user.es_admin()
        or user.es_supervisor
        or (user.esta_limitado_a_delegacion and user.delegacion_id)
    )


@login_required(login_url='web_login')
@require_POST
def web_anular_actividad(request, actividad_id):
    if not puede_anular_actividades(request.user):
        raise PermissionDenied

    actividades = Actividad.objects.all()
    if request.user.esta_limitado_a_delegacion:
        actividades = _filtrar_por_delegacion(actividades, request.user)
    actividad = get_object_or_404(actividades, pk=actividad_id)
    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        messages.error(request, 'Indica el motivo de la anulación.')
        return redirect(
            f"{reverse('web_personal')}?id={actividad.funcionario_id}"
            f"&periodo={request.POST.get('periodo', 'actual')}#historial-actividades"
        )

    with transaction.atomic():
        actividad = get_object_or_404(
            actividades.select_for_update(),
            pk=actividad_id,
        )
        estado_anterior = {
            'anulada': actividad.anulada,
            'motivo_anulacion': actividad.motivo_anulacion,
        }
        try:
            actividad.anular(request.user, motivo)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            return redirect(
                f"{reverse('web_personal')}?id={actividad.funcionario_id}"
                f"&periodo={request.POST.get('periodo', 'actual')}#historial-actividades"
            )
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
            ip_origen=obtener_ip_cliente(request) or None,
        )
    messages.success(
        request,
        f'La actividad {actividad.codigo_registro} fue anulada y excluida del avance.',
    )
    return redirect(
        f"{reverse('web_personal')}?id={actividad.funcionario_id}"
        f"&periodo={request.POST.get('periodo', 'actual')}#historial-actividades"
    )


def web_agregar_evidencia(request, actividad_id):
    if not request.user.is_authenticated:
        messages.error(request, 'Debe iniciar sesión para agregar evidencias.')
        return redirect('web_login')
    actividad = get_object_or_404(
        Actividad.objects.select_related('funcionario', 'delegacion'), id=actividad_id
    )
    if request.user.es_administrativo and actividad.funcionario_id != request.user.id:
        messages.error(request, 'Solo puedes agregar evidencias a tus propias actividades.')
        return redirect('web_personal')
    if request.user.esta_limitado_a_delegacion and actividad.delegacion_id != request.user.delegacion_id:
        messages.error(request, 'Solo puedes agregar evidencias a actividades de tu delegación.')
        return redirect('web_personal')
    if request.method != 'POST':
        return redirect(f"{reverse('web_personal')}?id={actividad.funcionario_id}")
    if actividad.anulada:
        messages.error(request, 'No se puede agregar evidencia a una actividad anulada.')
        return redirect(f"{reverse('web_personal')}?id={actividad.funcionario_id}")
    try:
        with transaction.atomic():
            actividad = Actividad.objects.select_for_update().get(pk=actividad.pk)
            if Evidencia.objects.filter(
                actividad=actividad,
                estado=Evidencia.Estado.APROBADA,
            ).exists():
                messages.error(
                    request,
                    'La actividad está cerrada porque ya tiene una evidencia aprobada.',
                )
                return redirect(f"{reverse('web_personal')}?id={actividad.funcionario_id}")
            fotos = request.FILES.getlist('fotos')
            if not fotos:
                messages.error(request, 'Selecciona al menos una fotografía para agregar evidencia.')
                return redirect(f"{reverse('web_personal')}?id={actividad.funcionario_id}")
            evidencias = _guardar_fotos(actividad, fotos, request.user)
            RegistroAuditoria.objects.create(
                usuario=request.user,
                accion='AGREGAR_EVIDENCIA',
                entidad='Actividad',
                identificador_registro=str(actividad.id),
                valor_nuevo={'evidencias': [e.codigo_verificador for e in evidencias]},
                ip_origen=obtener_ip_cliente(request) or None,
            )
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect(f"{reverse('web_personal')}?id={actividad.funcionario_id}")
    messages.success(request, f'Se agregaron {len(evidencias)} evidencia(s) a la actividad #{actividad.id}.')
    return redirect(f"{reverse('web_personal')}?id={actividad.funcionario_id}")


@login_required(login_url='web_login')
@require_POST
def web_comentar_actividad(request, actividad_id):
    actividad = get_object_or_404(
        Actividad.objects.select_related('funcionario', 'delegacion'),
        pk=actividad_id,
    )
    puede_comentar = (
        request.user.es_admin()
        or request.user.es_supervisor
        or (
            request.user.esta_limitado_a_delegacion
            and actividad.delegacion_id == request.user.delegacion_id
        )
        or (
            request.user.es_administrativo
            and actividad.funcionario_id == request.user.pk
        )
    )
    if not puede_comentar:
        raise PermissionDenied

    form = ComentarioTextoForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            '; '.join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )
    else:
        with transaction.atomic():
            comentario = ComentarioActividad.objects.create(
                actividad=actividad,
                autor=request.user,
                texto=form.cleaned_data['comentario'],
            )
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='CREACION',
                accion='COMENTAR_ACTIVIDAD',
                entidad='ComentarioActividad',
                identificador=str(comentario.pk),
                identificador_registro=str(actividad.pk),
                valor_nuevo={
                    'actividad_id': actividad.pk,
                    'texto': comentario.texto,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(request, 'Comentario agregado a la actividad.')
    periodo = request.POST.get('periodo', 'actual')
    return redirect(
        f"{reverse('web_personal')}?id={actividad.funcionario_id}"
        f"&periodo={periodo}#actividad-{actividad.pk}"
    )


@login_required(login_url='web_login')
@require_POST
def web_comentar_compromiso(request, compromiso_id):
    compromiso = get_object_or_404(
        CompromisoAgenda.objects.select_related('delegacion', 'responsable'),
        pk=compromiso_id,
    )
    if not _puede_gestionar_compromiso(request.user, compromiso):
        raise PermissionDenied
    form = ComentarioTextoForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            '; '.join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )
    else:
        with transaction.atomic():
            observacion = ObservacionCompromiso.objects.create(
                compromiso=compromiso,
                autor=request.user,
                texto=form.cleaned_data['comentario'],
            )
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                evento='CREACION',
                accion='COMENTAR_COMPROMISO',
                entidad='ObservacionCompromiso',
                identificador=str(observacion.pk),
                identificador_registro=str(compromiso.pk),
                valor_nuevo={
                    'compromiso_id': compromiso.pk,
                    'texto': observacion.texto,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(request, 'Comentario agregado al compromiso.')
    return redirect('web_editar_compromiso', compromiso_id=compromiso.pk)


def web_verificacion(request):
    if not request.user.is_authenticated:
        return redirect('web_login')

    if not request.user.puede_aprobar_evidencias:
        messages.warning(request, "Acceso restringido: La bandeja de Validación 'Check' está reservada para Delegados Municipales y Jefaturas (Segregación de Funciones RN-009).")
        return redirect('web_personal')

    delegaciones = _filtrar_por_delegacion(
        Delegacion.objects.filter(activo=True),
        request.user,
        campo='pk',
    )
    delegacion_filtro = request.GET.get('delegacion_id')
    estado_filtro = request.GET.get('estado', 'PENDIENTE')

    evidencias = Evidencia.objects.all()
    if request.user.esta_limitado_a_delegacion:
        if request.user.delegacion_id:
            evidencias = evidencias.filter(actividad__delegacion_id=request.user.delegacion_id)
        else:
            evidencias = evidencias.none()
    evidencias = evidencias.select_related(
        'actividad__delegacion', 'actividad__funcionario', 'verificador'
    ).order_by('-fecha_subida')

    if delegacion_filtro:
        try:
            evidencias = evidencias.filter(actividad__delegacion_id=int(delegacion_filtro))
        except ValueError:
            pass

    if estado_filtro and estado_filtro != 'TODOS':
        evidencias = evidencias.filter(estado=estado_filtro)

    return render(request, 'verificacion_check.html', {
        'evidencias': evidencias,
        'delegaciones': delegaciones,
        'delegacion_filtro': int(delegacion_filtro) if delegacion_filtro else None,
        'estado_filtro': estado_filtro,
    })

def web_validar_evidencia(request, evidencia_id):
    if (
        not request.user.is_authenticated
        or not request.user.puede_aprobar_evidencias
    ):
        messages.error(request, "Acceso denegado: Un funcionario no puede auto-aprobar evidencias. Solo Delegados Municipales, Coordinadores y Administradores tienen facultades de validación (Regla RN-009 y Criterio CA-07).")
        return redirect('web_verificacion')

    if request.method == 'POST':
        evidencias = Evidencia.objects.all()
        if request.user.esta_limitado_a_delegacion:
            if not request.user.delegacion_id:
                messages.error(request, 'Debes tener una delegación asignada para validar evidencias.')
                return redirect('web_verificacion')
            evidencias = evidencias.filter(actividad__delegacion_id=request.user.delegacion_id)
        evidencia = get_object_or_404(
            evidencias.select_related(
                'actividad__funcionario',
                'actividad__delegacion',
                'actividad__item_medicion__periodo',
            ),
            id=evidencia_id,
        )
        if evidencia.es_autovalidacion(request.user):
            messages.error(request, Evidencia.MENSAJE_AUTOVALIDACION)
            return redirect('web_verificacion')
        if evidencia.estado not in (
            Evidencia.Estado.PENDIENTE,
            Evidencia.Estado.CORRECCION,
        ):
            messages.error(
                request,
                'Esta evidencia ya fue aprobada o rechazada y no admite otra decisión.',
            )
            return redirect('web_verificacion')
        form = ValidarEvidenciaForm(request.POST)
        if not form.is_valid():
            messages.error(
                request,
                '; '.join(
                    error
                    for errors in form.errors.values()
                    for error in errors
                ),
            )
            return redirect('web_verificacion')

        decision = form.cleaned_data['decision']
        observacion = form.cleaned_data['observacion'].strip()
        with transaction.atomic():
            estado_anterior = evidencia.estado
            meta_avances_anteriores = []
            if evidencia.actividad.item_medicion_id:
                metas = MetaModel.objects.filter(
                    item_id=evidencia.actividad.item_medicion_id,
                    periodo_id=evidencia.actividad.item_medicion.periodo_id,
                    vigencia=True,
                ).prefetch_related('indicadores')
                meta_avances_anteriores = [
                    {
                        'meta_id': meta.pk,
                        'avance': meta.calcular_meta(),
                    }
                    for meta in metas
                ]

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

            meta_avances_nuevos = _recalcular_metas_evidencia(evidencia)
            metrics_snapshot = None
            item = evidencia.actividad.item_medicion
            if item:
                metricas = calcular_metricas_funcionario(
                    evidencia.actividad.funcionario,
                    item.periodo,
                    timezone.localdate(),
                )
                metricas_item = next(
                    (
                        entry for entry in metricas['items']
                        if entry['item_id'] == item.pk
                    ),
                    None,
                )
                if metricas_item:
                    metrics_snapshot = {
                        'item_id': item.pk,
                        'avance_aprobado': metricas_item['avance_aprobado'],
                        'cumplimiento_pct': metricas_item['cumplimiento_pct'],
                        'semaforo': metricas_item['semaforo'],
                    }

            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='VALIDAR_EVIDENCIA',
                evento='APROBACION' if decision == Evidencia.Estado.APROBADA else 'MODIFICACION',
                entidad='Evidencia',
                identificador=str(evidencia.pk),
                identificador_registro=str(evidencia.pk),
                valor_anterior={
                    'estado': estado_anterior,
                    'metas': meta_avances_anteriores,
                },
                valor_nuevo={
                    'estado': decision,
                    'observacion': observacion,
                    'codigo': evidencia.codigo_verificador,
                    'metas': meta_avances_nuevos,
                    'metricas_item': metrics_snapshot,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )

        messages.success(
            request,
            f"Evidencia {evidencia.codigo_verificador} validada como {evidencia.get_estado_display()}.",
        )

    return redirect('web_verificacion')

def web_tubo(request):
    periodos = PeriodoMedicion.objects.order_by('-fecha_inicio', '-pk')
    periodo_activo = get_periodo_activo()
    periodo_filtro = request.GET.get('periodo')
    if periodo_filtro == 'todos':
        periodo_seleccionado = None
    elif periodo_filtro:
        periodo_seleccionado = get_object_or_404(periodos, pk=periodo_filtro)
    else:
        periodo_seleccionado = periodo_activo if periodo_activo and periodo_activo.pk else None

    delegaciones = Delegacion.objects.filter(activo=True)
    if request.user.es_administrativo:
        delegaciones = delegaciones.filter(pk=request.user.delegacion_id) if request.user.delegacion_id else delegaciones.none()
    else:
        delegaciones = _filtrar_por_delegacion(delegaciones, request.user, campo='pk')
    usuarios_terreno = Usuario.objects.filter(
        is_active=True,
    ).select_related('delegacion')
    if request.user.es_administrativo:
        usuarios_terreno = usuarios_terreno.filter(pk=request.user.pk)
    else:
        usuarios_terreno = _filtrar_por_delegacion(
            usuarios_terreno,
            request.user,
        )
    hoy = timezone.now().date()

    compromisos_queryset = CompromisoAgenda.objects.all()
    if request.user.es_administrativo:
        # CU-33 (HU-12): agenda compartida; el Funcionario ve los compromisos de su
        # delegación, aunque solo gestiona los propios.
        ambito = Q(responsable=request.user) | Q(creado_por=request.user)
        if request.user.delegacion_id:
            ambito |= Q(delegacion_id=request.user.delegacion_id)
        compromisos_queryset = compromisos_queryset.filter(ambito)
    else:
        compromisos_queryset = _filtrar_por_delegacion(
            compromisos_queryset,
            request.user,
        )
    # CU-25 / CU-33: filtros por responsable, territorio y rango de fechas.
    responsables_filtro = Usuario.objects.filter(
        pk__in=compromisos_queryset.values('responsable_id'),
    ).order_by('first_name', 'last_name', 'username')
    territorios_filtro = sorted(
        set(compromisos_queryset.values_list('territorio', flat=True)) - {'', None}
    )
    filtro_responsable = request.GET.get('responsable', '')
    filtro_estado = request.GET.get('estado', '')
    filtro_territorio = request.GET.get('territorio', '')
    filtro_area = request.GET.get('area', '')
    filtro_desde = parse_date(request.GET.get('fecha_desde', '') or '')
    filtro_hasta = parse_date(request.GET.get('fecha_hasta', '') or '')
    if filtro_responsable.isdecimal():
        compromisos_queryset = compromisos_queryset.filter(responsable_id=int(filtro_responsable))
    if filtro_territorio:
        compromisos_queryset = compromisos_queryset.filter(territorio=filtro_territorio)
    if filtro_area:
        compromisos_queryset = compromisos_queryset.filter(area_apoyo=filtro_area)
    if filtro_desde:
        compromisos_queryset = compromisos_queryset.filter(fecha_comprometida__gte=filtro_desde)
    if filtro_hasta:
        compromisos_queryset = compromisos_queryset.filter(fecha_comprometida__lte=filtro_hasta)
    if periodo_seleccionado:
        compromisos_queryset = compromisos_queryset.filter(
            fecha_comprometida__range=(
                periodo_seleccionado.fecha_inicio,
                periodo_seleccionado.fecha_termino,
            )
        )
    compromisos = list(compromisos_queryset.select_related(
        'delegacion', 'responsable', 'creado_por', 'actividad'
    ).prefetch_related(
        'actividad__evidencias',
        'historial_observaciones__autor',
    ).order_by('fecha_comprometida'))
    estados_filtro = [
        ('INGRESADO', 'Ingresado'), ('PENDIENTE', 'Pendiente'), ('EN_PROCESO', 'En proceso'),
        ('REPROGRAMADO', 'Reprogramado'), ('VENCIDO', 'Vencido'), ('REALIZADO', 'Realizado'),
    ]
    if filtro_estado:
        realizados = {
            CompromisoAgenda.Estado.COMPLETADO,
            CompromisoAgenda.Estado.REALIZADO,
            CompromisoAgenda.Estado.CUMPLIDO,
        }
        compromisos = [
            c for c in compromisos
            if (c.estado_tablero in realizados if filtro_estado == 'REALIZADO'
                else c.estado_tablero == filtro_estado)
        ]
    actividades_queryset = Actividad.objects.select_related(
        'funcionario', 'delegacion'
    ).filter(anulada=False)
    if request.user.es_administrativo:
        actividades_queryset = actividades_queryset.filter(funcionario=request.user)
    else:
        actividades_queryset = _filtrar_por_delegacion(
            actividades_queryset,
            request.user,
        )
    # Resumen colectivo por Colaborador (Slide 14 de la presentación municipal)
    resumen_colaboradores = []
    totales_colectivos = {
        'ingresado': 0,
        'pendiente': 0,
        'en_proceso': 0,
        'reprogramado': 0,
        'vencido': 0,
        'realizado': 0,
        'realizado_fuera_plazo': 0,
        'total': 0
    }

    resumen_por_usuario = {}
    for compromiso in compromisos:
        # Editar, cambiar estado y anular solo para quien puede gestionarlo.
        compromiso.puede_anular = _puede_gestionar_compromiso(request.user, compromiso)
        compromiso.puede_gestionar = compromiso.puede_anular
        resumen = resumen_por_usuario.setdefault(compromiso.responsable_id, {
            'usuario': compromiso.responsable,
            'ingresado': 0, 'pendiente': 0, 'en_proceso': 0,
            'reprogramado': 0, 'vencido': 0, 'realizado': 0,
            'realizado_fuera_plazo': 0,
            'total': 0,
        })
        estado = compromiso.estado_tablero
        if estado == CompromisoAgenda.Estado.VENCIDO:
            resumen['vencido'] += 1
        elif estado == CompromisoAgenda.Estado.INGRESADO:
            resumen['ingresado'] += 1
        elif estado == CompromisoAgenda.Estado.PENDIENTE:
            resumen['pendiente'] += 1
        elif estado == CompromisoAgenda.Estado.EN_PROCESO:
            resumen['en_proceso'] += 1
        elif estado == CompromisoAgenda.Estado.REPROGRAMADO:
            resumen['reprogramado'] += 1
        elif estado in [
            CompromisoAgenda.Estado.COMPLETADO,
            CompromisoAgenda.Estado.REALIZADO,
            CompromisoAgenda.Estado.CUMPLIDO,
        ]:
            resumen['realizado'] += 1
        if compromiso.realizado_fuera_de_plazo:
            resumen['realizado_fuera_plazo'] += 1
        resumen['total'] += 1

    # RNF-015: el umbral de cumplimiento se toma del período (umbral colectivo).
    periodo_umbral = periodo_seleccionado or periodo_activo
    umbral_cumplimiento = (
        parametros_calculo(periodo_umbral).umbral_colectivo
        if periodo_umbral and getattr(periodo_umbral, 'pk', None)
        else 80.0
    )
    for resumen in resumen_por_usuario.values():
        u = resumen['usuario']
        pct = round((resumen['realizado'] / resumen['total']) * 100.0, 1) if resumen['total'] > 0 else 0.0
        pct_pendiente = round(
            ((resumen['total'] - resumen['realizado']) / resumen['total']) * 100.0,
            1,
        ) if resumen['total'] > 0 else 0.0
        cumple = (pct >= umbral_cumplimiento)
        resumen_colaboradores.append({
            'usuario': u,
            'nombre': u.get_full_name() or u.username,
            'delegacion': u.delegacion.codigo if u.delegacion else 'Central',
            'ingresado': resumen['ingresado'],
            'pendiente': resumen['pendiente'],
            'en_proceso': resumen['en_proceso'],
            'reprogramado': resumen['reprogramado'],
            'realizado': resumen['realizado'],
            'realizado_fuera_plazo': resumen['realizado_fuera_plazo'],
            'total': resumen['total'],
            'pct_realizado': pct,
            'pct_pendiente': pct_pendiente,
            'cumple': cumple
        })
        for key in [
            'ingresado',
            'pendiente',
            'en_proceso',
            'reprogramado',
            'vencido',
            'realizado',
            'realizado_fuera_plazo',
            'total',
        ]:
            totales_colectivos[key] += resumen[key]

    if totales_colectivos['total'] > 0:
        totales_colectivos['pct_realizado'] = round((totales_colectivos['realizado'] / totales_colectivos['total']) * 100.0, 1)
        totales_colectivos['pct_pendiente'] = round(
            (
                (totales_colectivos['total'] - totales_colectivos['realizado'])
                / totales_colectivos['total']
            ) * 100.0,
            1,
        )
        totales_colectivos['cumple'] = (totales_colectivos['pct_realizado'] >= umbral_cumplimiento)
    else:
        totales_colectivos['pct_realizado'] = 0.0
        totales_colectivos['pct_pendiente'] = 0.0
        totales_colectivos['cumple'] = False

    return render(request, 'tubo_trabajo.html', {
        'delegaciones': delegaciones,
        'usuarios_terreno': usuarios_terreno,
        'hoy': hoy,
        'todos_los_compromisos': compromisos,
        'ingresados': [c for c in compromisos if c.estado_tablero == CompromisoAgenda.Estado.INGRESADO],
        'pendientes': [c for c in compromisos if c.estado_tablero == CompromisoAgenda.Estado.PENDIENTE],
        'en_proceso': [c for c in compromisos if c.estado_tablero == CompromisoAgenda.Estado.EN_PROCESO],
        'realizados': [c for c in compromisos if c.estado_tablero in [CompromisoAgenda.Estado.COMPLETADO, CompromisoAgenda.Estado.REALIZADO, CompromisoAgenda.Estado.CUMPLIDO]],
        'vencidos': [c for c in compromisos if c.estado_tablero == CompromisoAgenda.Estado.VENCIDO],
        'reprogramados': [c for c in compromisos if c.estado_tablero == CompromisoAgenda.Estado.REPROGRAMADO],
        'periodo': periodo_activo,
        'periodos': periodos,
        'periodo_seleccionado': periodo_seleccionado,
        'periodo_todos': periodo_filtro == 'todos' or periodo_seleccionado is None,
        'fecha_min_periodo': periodo_activo.fecha_inicio if periodo_activo else hoy,
        'fecha_max_periodo': periodo_activo.fecha_termino if periodo_activo else hoy,
        'actividades': actividades_queryset.order_by('-fecha_actividad')[:200],
        'actividades_sin_compromiso': actividades_queryset.filter(
            compromiso_agenda__isnull=True,
            compromisos__isnull=True,
        ).order_by('-fecha_actividad', '-pk'),
        'territorios': [
            'Sector Centro', 'El Milagro', 'La Pampa', 'Las Compañías',
            'La Antena', 'Avenida del Mar', 'Sector Rural', 'Otro',
        ],
        'resumen_colaboradores': resumen_colaboradores,
        'totales_colectivos': totales_colectivos,
        'puede_reactivar_compromisos': request.user.es_admin(),
        'responsables_filtro': responsables_filtro,
        'territorios_filtro': territorios_filtro,
        'estados_filtro': estados_filtro,
        'filtro_responsable': filtro_responsable,
        'filtro_estado': filtro_estado,
        'filtro_territorio': filtro_territorio,
        'filtro_area': filtro_area,
        'filtro_desde': filtro_desde,
        'filtro_hasta': filtro_hasta,
        'compromisos_anulados': (
            CompromisoAgenda.todos.filter(anulado=True)
            .select_related('delegacion', 'responsable', 'anulado_por')
            .order_by('-anulado_en')[:200]
            if request.user.es_admin()
            else []
        ),
    })

def web_cambiar_estado_compromiso(request, compromiso_id):
    if not request.user.is_authenticated:
        messages.error(request, "Debe iniciar sesión para modificar el estado de un compromiso.")
        return redirect('web_login')

    if request.method == 'POST':
        compromiso = get_object_or_404(
            CompromisoAgenda.objects.select_related('actividad'),
            id=compromiso_id,
        )
        if not _puede_gestionar_compromiso(request.user, compromiso):
            raise PermissionDenied
        form = CompromisoEstadoForm(request.POST, compromiso=compromiso)
        if not form.is_valid():
            messages.error(
                request,
                '; '.join(
                    error for errors in form.errors.values() for error in errors
                ),
            )
            return redirect('web_tubo')

        with transaction.atomic():
            compromiso = get_object_or_404(
                CompromisoAgenda.objects.select_for_update().select_related('actividad'),
                pk=compromiso_id,
            )
            if not _puede_gestionar_compromiso(request.user, compromiso):
                raise PermissionDenied
            estado_anterior = compromiso.estado
            observacion_anterior = compromiso.observacion or ''
            nuevo_estado = form.cleaned_data['estado']
            observacion = form.cleaned_data['observacion']
            try:
                compromiso.registrar_transicion(
                    nuevo_estado,
                    request.user,
                    observacion,
                )
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
                return redirect('web_tubo')
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='CAMBIO_ESTADO_COMPROMISO',
                evento='MODIFICACION',
                entidad='CompromisoAgenda',
                identificador=str(compromiso.pk),
                identificador_registro=str(compromiso.pk),
                valor_anterior={
                    'estado': estado_anterior,
                    'observacion': observacion_anterior,
                    'responsable_id': compromiso.responsable_id,
                },
                valor_nuevo={
                    'estado': nuevo_estado,
                    'observacion': observacion,
                    'fecha_realizacion': (
                        compromiso.fecha_realizacion.isoformat()
                        if compromiso.fecha_realizacion
                        else None
                    ),
                    'responsable_id': compromiso.responsable_id,
                },
                ip_origen=obtener_ip_cliente(request) or None,
            )
        messages.success(
            request,
            f"Compromiso #{compromiso.id} movido a {compromiso.get_estado_display()}.",
        )
    return redirect('web_tubo')

def web_crear_compromiso(request):
    if not request.user.is_authenticated:
        messages.error(request, "Debe iniciar sesión para registrar nuevos compromisos.")
        return redirect('web_login')

    if request.method == 'POST':
        solicitante = (request.POST.get('solicitante') or '').strip() or 'Vecino La Serena'
        origen = request.POST.get('origen') or 'INT'
        territorio = (request.POST.get('territorio') or '').strip() or 'La Serena'
        delegacion_id = request.POST.get('delegacion_id') or request.user.delegacion_id
        if not request.user.es_admin() and not request.user.es_supervisor and (
            not request.user.delegacion_id or str(delegacion_id) != str(request.user.delegacion_id)
        ):
            raise PermissionDenied
        responsable_id = request.POST.get('responsable_id') or request.user.id
        if request.user.es_administrativo:
            responsable_id = request.user.id
        elif request.user.esta_limitado_a_delegacion:
            responsable = get_object_or_404(Usuario, id=responsable_id)
            if responsable.delegacion_id != request.user.delegacion_id:
                raise PermissionDenied
        if request.user.rol not in (
            Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPERVISOR, Usuario.Rol.ADMINISTRATIVO
        ) and not request.user.is_superuser:
            raise PermissionDenied
        periodo = get_periodo_activo()
        try:
            fecha_comp = datetime.strptime(request.POST.get('fecha_comprometida', ''), '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Ingresa una fecha de compromiso válida.')
            return redirect('web_tubo')
        if not _fecha_en_periodo(fecha_comp, periodo):
            messages.error(request, 'La fecha del compromiso debe estar dentro del período trimestral activo.')
            return redirect('web_tubo')
        area_apoyo = (request.POST.get('area_apoyo') or '').strip() or 'Operaciones y Territorio'
        descripcion = (request.POST.get('descripcion') or '').strip() or 'Compromiso vecinal registrado en terreno'
        observaciones = (request.POST.get('observaciones') or '').strip()
        estado = request.POST.get('estado') or CompromisoAgenda.Estado.PENDIENTE

        with transaction.atomic():
            c = CompromisoAgenda.objects.create(
                solicitante=solicitante,
                origen=origen,
                territorio=territorio,
                delegacion_id=delegacion_id,
                responsable_id=responsable_id,
                fecha_compromiso=fecha_comp,
                fecha_comprometida=fecha_comp,
                area_apoyo=area_apoyo,
                descripcion=descripcion,
                observacion=observaciones,
                estado=estado if estado in [
                    CompromisoAgenda.Estado.INGRESADO, CompromisoAgenda.Estado.PENDIENTE,
                    CompromisoAgenda.Estado.EN_PROCESO,
                ] else CompromisoAgenda.Estado.PENDIENTE,
                creado_por=request.user
            )
            RegistroAuditoria.objects.create(
                usuario=request.user,
                funcionario=request.user,
                accion='CREAR',
                entidad='CompromisoAgenda',
                identificador_registro=str(c.id),
                valor_nuevo={
                    'solicitante': c.solicitante,
                    'responsable': c.responsable.username,
                    'fecha': str(c.fecha_comprometida),
                },
                ip_origen=obtener_ip_cliente(request) or None
            )
        messages.success(request, f"Nuevo compromiso #{c.id} guardado exitosamente en el Tubo de Trabajo.")
    return redirect('web_tubo')


def _compromiso_audit_snapshot(compromiso):
    return {
        'solicitante': compromiso.solicitante,
        'origen': compromiso.origen,
        'territorio': compromiso.territorio,
        'delegacion_id': compromiso.delegacion_id,
        'responsable_id': compromiso.responsable_id,
        'fecha_comprometida': str(compromiso.fecha_comprometida),
        'area_apoyo': compromiso.area_apoyo,
        'descripcion': compromiso.descripcion,
        'estado': compromiso.estado,
        'observacion': compromiso.observacion or '',
        'actividad_id': compromiso.actividad_id,
    }


class CompromisoCreateView(LoginRequiredMixin, CreateView):
    model = CompromisoAgenda
    form_class = CompromisoAgendaForm
    template_name = 'compromiso_form.html'
    success_url = reverse_lazy('web_tubo')

    def dispatch(self, request, *args, **kwargs):
        if not (
            request.user.is_superuser
            or request.user.rol in (
                Usuario.Rol.ADMINISTRADOR,
                Usuario.Rol.SUPERVISOR,
                Usuario.Rol.ADMINISTRATIVO,
            )
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(user=self.request.user, periodo=get_periodo_activo())
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        user = self.request.user
        initial.update({
            'delegacion': user.delegacion_id,
            'responsable': user.pk,
            'fecha_comprometida': timezone.localdate() + timedelta(days=1),
            'estado': CompromisoAgenda.Estado.PENDIENTE,
            'origen': 'INT',
        })
        actividad_id = self.request.GET.get('actividad_id', '')
        if actividad_id.isdecimal():
            actividades = Actividad.objects.filter(pk=int(actividad_id))
            if user.es_administrativo:
                actividades = actividades.filter(funcionario=user)
            elif user.esta_limitado_a_delegacion:
                actividades = actividades.filter(delegacion_id=user.delegacion_id)
            actividad = actividades.first()
            if actividad:
                initial['actividad'] = actividad.pk
                initial['delegacion'] = actividad.delegacion_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'titulo': 'Registrar compromiso',
            'periodo': get_periodo_activo(),
        })
        return context

    def form_valid(self, form):
        with transaction.atomic():
            compromiso = form.save(commit=False)
            compromiso.creado_por = self.request.user
            compromiso.save()
            form.save_m2m()
            if compromiso.actividad_id:
                compromiso.actividad.compromiso_agenda = compromiso
                compromiso.actividad.save(
                    update_fields=['compromiso_agenda', 'actualizado_en']
                )
            self.object = compromiso
            RegistroAuditoria.objects.create(
                usuario=self.request.user,
                funcionario=self.request.user,
                accion='CREAR_COMPROMISO',
                evento='CREACION',
                entidad='CompromisoAgenda',
                identificador=str(compromiso.pk),
                identificador_registro=str(compromiso.pk),
                valor_anterior=None,
                valor_nuevo=_compromiso_audit_snapshot(compromiso),
                ip_origen=obtener_ip_cliente(self.request) or None,
            )
            if compromiso.observacion:
                ObservacionCompromiso.objects.create(
                    compromiso=compromiso,
                    autor=self.request.user,
                    texto=compromiso.observacion,
                )
        messages.success(
            self.request,
            f'Compromiso #{compromiso.pk} creado y registrado en auditoría.',
        )
        return redirect(self.get_success_url())


class CompromisoUpdateView(LoginRequiredMixin, ConcurrenciaContextMixin, UpdateView):
    model = CompromisoAgenda
    form_class = CompromisoAgendaForm
    template_name = 'compromiso_form.html'
    pk_url_kwarg = 'compromiso_id'
    success_url = reverse_lazy('web_tubo')

    def get_object(self, queryset=None):
        compromiso = super().get_object(queryset)
        if not _puede_gestionar_compromiso(self.request.user, compromiso):
            raise PermissionDenied
        self.valor_anterior = _compromiso_audit_snapshot(compromiso)
        return compromiso

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(user=self.request.user, periodo=get_periodo_activo())
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'titulo': f'Actualizar compromiso #{self.object.pk}',
            'periodo': get_periodo_activo(),
            'historial_observaciones': self.object.historial_observaciones.select_related(
                'autor'
            ).all(),
        })
        return context

    def _validar_fotos_compromiso(self, form, fotos, reemplazar):
        """Las fotos se guardan como evidencias de la actividad asociada al compromiso."""
        if reemplazar and not fotos:
            form.add_error(None, 'Selecciona las fotografías nuevas para reemplazar las actuales.')
            return None
        if not fotos:
            return None
        actividad = form.cleaned_data.get('actividad')
        if not actividad:
            form.add_error(
                'actividad',
                'Para adjuntar fotografías, asocia una actividad al compromiso.',
            )
            return None
        if actividad.anulada:
            form.add_error('actividad', 'No se puede agregar evidencia a una actividad anulada.')
            return None
        try:
            _validar_fotos(fotos)
        except ValidationError as exc:
            form.add_error(None, '; '.join(exc.messages))
            return None
        return actividad

    def form_valid(self, form):
        fotos = self.request.FILES.getlist('fotos')
        reemplazar = bool(self.request.POST.get('reemplazar_fotos'))
        actividad_fotos = self._validar_fotos_compromiso(form, fotos, reemplazar)
        if form.errors:
            return self.form_invalid(form)

        with transaction.atomic():
            # RNF-003: rechaza el cambio si otra persona editó el compromiso.
            if self.hay_conflicto():
                return self.rechazar_por_conflicto(form)
            snapshot_anterior = self.valor_anterior
            actividad_anterior = self.object.actividad
            compromiso = form.save(commit=False)
            compromiso.save()
            form.save_m2m()
            evidencias_reemplazadas = []
            evidencias_nuevas = []
            if actividad_fotos:
                if reemplazar:
                    # Normativa: las fotos anteriores no se borran; quedan en el historial.
                    for evidencia in actividad_fotos.evidencias.all():
                        evidencia.reemplazar(self.request.user)
                        evidencias_reemplazadas.append(evidencia.codigo_verificador)
                evidencias_nuevas = [
                    evidencia.codigo_verificador
                    for evidencia in _guardar_fotos(actividad_fotos, fotos, self.request.user)
                ]
                RegistroAuditoria.objects.create(
                    usuario=self.request.user,
                    funcionario=self.request.user,
                    accion='ADJUNTAR_EVIDENCIA_COMPROMISO',
                    evento='CREACION',
                    entidad='Evidencia',
                    identificador=str(actividad_fotos.pk),
                    identificador_registro=str(actividad_fotos.pk),
                    valor_anterior=(
                        {'evidencias_reemplazadas': evidencias_reemplazadas}
                        if evidencias_reemplazadas
                        else None
                    ),
                    valor_nuevo={
                        'compromiso_id': compromiso.pk,
                        'evidencias_nuevas': evidencias_nuevas,
                    },
                    ip_origen=obtener_ip_cliente(self.request) or None,
                )
            if (
                actividad_anterior
                and actividad_anterior != compromiso.actividad
                and actividad_anterior.compromiso_agenda_id == compromiso.pk
            ):
                actividad_anterior.compromiso_agenda = None
                actividad_anterior.save(
                    update_fields=['compromiso_agenda', 'actualizado_en']
                )
            if (
                compromiso.actividad_id
                and compromiso.actividad.compromiso_agenda_id != compromiso.pk
            ):
                compromiso.actividad.compromiso_agenda = compromiso
                compromiso.actividad.save(
                    update_fields=['compromiso_agenda', 'actualizado_en']
                )
            self.object = compromiso
            snapshot_nuevo = _compromiso_audit_snapshot(compromiso)
            RegistroAuditoria.objects.create(
                usuario=self.request.user,
                funcionario=self.request.user,
                accion='REASIGNAR_ACTUALIZAR_COMPROMISO',
                evento='MODIFICACION',
                entidad='CompromisoAgenda',
                identificador=str(compromiso.pk),
                identificador_registro=str(compromiso.pk),
                valor_anterior=snapshot_anterior,
                valor_nuevo=snapshot_nuevo,
                ip_origen=obtener_ip_cliente(self.request) or None,
            )
            if (
                snapshot_anterior['observacion'] != snapshot_nuevo['observacion']
                and snapshot_nuevo['observacion']
            ):
                ObservacionCompromiso.objects.create(
                    compromiso=compromiso,
                    autor=self.request.user,
                    texto=snapshot_nuevo['observacion'],
                )
        mensaje = f'Compromiso #{compromiso.pk} actualizado y auditado.'
        if evidencias_nuevas:
            mensaje += (
                f' Se agregaron {len(evidencias_nuevas)} fotografía(s), pendientes de validación.'
            )
        if evidencias_reemplazadas:
            mensaje += (
                f' {len(evidencias_reemplazadas)} fotografía(s) anterior(es) quedaron en el historial.'
            )
        messages.success(self.request, mensaje)
        return redirect(self.get_success_url())

@login_required(login_url='web_login')
@require_POST
def web_anular_compromiso(request, compromiso_id):
    """Baja lógica: por normativa los compromisos no se eliminan, se anulan con motivo."""
    compromiso = get_object_or_404(CompromisoAgenda, id=compromiso_id)
    if not _puede_gestionar_compromiso(request.user, compromiso):
        raise PermissionDenied

    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        messages.error(request, 'Indica el motivo de la anulación.')
        return redirect('web_tubo')

    with transaction.atomic():
        compromiso = get_object_or_404(
            CompromisoAgenda.objects.select_for_update(),
            id=compromiso_id,
        )
        try:
            compromiso.anular(request.user, motivo)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            return redirect('web_tubo')
        RegistroAuditoria.objects.create(
            usuario=request.user,
            funcionario=request.user,
            accion='ANULAR_COMPROMISO',
            evento='MODIFICACION',
            entidad='CompromisoAgenda',
            identificador=str(compromiso.pk),
            identificador_registro=str(compromiso.pk),
            valor_anterior={'anulado': False, 'estado': compromiso.estado},
            valor_nuevo={
                'anulado': True,
                'motivo_anulacion': compromiso.motivo_anulacion,
                'solicitante': compromiso.solicitante,
            },
            ip_origen=obtener_ip_cliente(request) or None,
        )
    messages.success(
        request,
        f"Compromiso #{compromiso.pk} de '{compromiso.solicitante}' anulado. "
        "Se conserva en el historial.",
    )
    return redirect('web_tubo')


@login_required(login_url='web_login')
@require_POST
def web_reactivar_compromiso(request, compromiso_id):
    """Solo el Administrador revierte una anulación, con motivo y auditoría."""
    if not request.user.es_admin():
        raise PermissionDenied

    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        messages.error(request, 'Indica el motivo de la reactivación.')
        return redirect(f"{reverse('web_tubo')}#tab-anulados")

    with transaction.atomic():
        compromiso = get_object_or_404(
            CompromisoAgenda.todos.select_for_update(),
            id=compromiso_id,
            anulado=True,
        )
        anulacion = {
            'anulado': True,
            'anulado_en': compromiso.anulado_en.isoformat() if compromiso.anulado_en else None,
            'anulado_por_id': compromiso.anulado_por_id,
            'motivo_anulacion': compromiso.motivo_anulacion,
        }
        compromiso.reactivar()
        RegistroAuditoria.objects.create(
            usuario=request.user,
            funcionario=request.user,
            accion='REACTIVAR_COMPROMISO',
            evento='MODIFICACION',
            entidad='CompromisoAgenda',
            identificador=str(compromiso.pk),
            identificador_registro=str(compromiso.pk),
            valor_anterior=anulacion,
            valor_nuevo={'anulado': False, 'motivo_reactivacion': motivo},
            ip_origen=obtener_ip_cliente(request) or None,
        )
    messages.success(
        request,
        f"Compromiso #{compromiso.pk} de '{compromiso.solicitante}' reactivado.",
    )
    return redirect('web_tubo')

def web_terreno(request):
    if not request.user.is_authenticated:
        return redirect('web_login')

    periodo = get_periodo_activo()
    items_medicion = ItemMedicion.objects.none()
    if periodo and periodo.pk and request.user.cargo_id:
        items_medicion = ItemMedicion.objects.filter(
            periodo=periodo,
            activo=True,
            cargo=request.user.cargo,
        )
    actividades = Actividad.objects.select_related(
        'delegacion', 'funcionario', 'item', 'item_medicion'
    ).order_by('-fecha_actividad')
    if request.user.es_administrativo:
        actividades = actividades.filter(funcionario=request.user)
    elif request.user.esta_limitado_a_delegacion:
        actividades = _filtrar_por_delegacion(actividades, request.user)

    if request.method == 'POST':
        actividad_id = request.POST.get('actividad_id')
        if actividad_id:
            actividad = get_object_or_404(actividades, id=actividad_id)
            fotos = request.FILES.getlist('fotos')
            if not fotos:
                messages.error(request, 'Selecciona al menos una fotografía para adjuntarla a la actividad.')
                return redirect('web_terreno')
            try:
                with transaction.atomic():
                    evidencias = _guardar_fotos(actividad, fotos, request.user)
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
                return redirect('web_terreno')
            messages.success(request, f'Se agregaron {len(evidencias)} evidencia(s) a la actividad #{actividad.id}.')
            return redirect('web_personal')

        direccion = (request.POST.get('direccion') or '').strip()
        accion = (request.POST.get('accion_ejecutada') or '').strip()
        telefono = (request.POST.get('contacto_telefono') or '').strip()
        fotos = request.FILES.getlist('fotos')
        validation_errors = []
        if not direccion:
            validation_errors.append(('Dirección', 'Este campo es obligatorio.'))
        if not accion:
            validation_errors.append(('Acción ejecutada', 'Este campo es obligatorio.'))
        if not fotos:
            validation_errors.append(('Fotografías', 'Adjunta al menos una fotografía.'))
        elif len(fotos) > MAX_FOTOS_POR_REGISTRO:
            validation_errors.append(('Fotografías', MENSAJE_MAX_FOTOS))
        if not telefono:
            validation_errors.append(('Teléfono de contacto', 'Este campo es obligatorio.'))
            contacto_telefono = ''
        else:
            try:
                contacto_telefono = normalize_chilean_phone(telefono)
            except ValidationError as exc:
                validation_errors.append(
                    ('Teléfono de contacto', '; '.join(exc.messages))
                )
                contacto_telefono = ''
        item_id = request.POST.get('item_medicion')
        item = (
            items_medicion.filter(pk=int(item_id)).first()
            if item_id and item_id.isdecimal()
            else None
        )
        if item_id and not item:
            validation_errors.append(
                ('Ítem de medición', 'No corresponde al período o cargo actual.')
            )
        elif not item_id and items_medicion.exists():
            validation_errors.append(
                ('Ítem de medición', 'Selecciona un ítem vigente para el período y cargo actual.')
            )

        fecha_actividad = timezone.localdate()
        if item and not (
            item.periodo.fecha_inicio <= fecha_actividad <= item.periodo.fecha_termino
        ):
            validation_errors.append(
                ('Fecha de actividad', 'Debe pertenecer al período del ítem seleccionado.')
            )

        latitud_raw = request.POST.get('latitud', '').strip()
        longitud_raw = request.POST.get('longitud', '').strip()
        latitud = longitud = None
        if bool(latitud_raw) != bool(longitud_raw):
            validation_errors.append(
                ('Ubicación', 'Debes ingresar latitud y longitud conjuntamente.')
            )
        elif latitud_raw:
            try:
                latitud = float(latitud_raw)
                longitud = float(longitud_raw)
                if (
                    not isfinite(latitud)
                    or not isfinite(longitud)
                    or not -90 <= latitud <= 90
                    or not -180 <= longitud <= 180
                ):
                    validation_errors.append(
                        ('Ubicación', 'La latitud y longitud deben estar dentro de rangos válidos.')
                    )
            except (TypeError, ValueError):
                validation_errors.append(
                    ('Ubicación', 'Ingresa coordenadas numéricas válidas.')
                )

        if validation_errors:
            for field, error in validation_errors:
                messages.error(request, f'{field}: {error}')
            return redirect('web_terreno')

        delegacion = request.user.delegacion or Delegacion.objects.first()
        if not delegacion:
            messages.error(request, 'No hay una delegación configurada para registrar la actividad.')
            return redirect('web_terreno')
        try:
            with transaction.atomic():
                actividad = Actividad.objects.create(
                    funcionario=request.user,
                    delegacion=delegacion,
                    item_medicion=item,
                    fecha_actividad=fecha_actividad,
                    contacto_telefono=contacto_telefono,
                    direccion=direccion,
                    latitud=latitud,
                    longitud=longitud,
                    actividad_solicitud=f'Actividad en terreno: {direccion}',
                    solicitud_problema=f'Actividad en terreno: {direccion}',
                    accion_ejecutada=accion,
                    accion=accion,
                )
                evidencias = _guardar_fotos(actividad, fotos, request.user)
                RegistroAuditoria.objects.create(
                    usuario=request.user,
                    funcionario=request.user,
                    accion='REGISTRO_ACTIVIDAD_TERRENO',
                    entidad='Actividad',
                    identificador_registro=str(actividad.id),
                    valor_nuevo={
                        'actividad_id': actividad.id,
                        'delegacion_id': delegacion.id,
                        'evidencias': [e.codigo_verificador for e in evidencias],
                    },
                    ip_origen=obtener_ip_cliente(request) or None,
                )
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            return redirect('web_terreno')
        messages.success(request, f'Actividad #{actividad.id} registrada con {len(evidencias)} evidencia(s).')
        return redirect('web_personal')

    return render(request, 'terreno_movil.html', {
        'items_medicion': items_medicion,
        'actividades_existentes': actividades[:200],
    })

@login_required(login_url='web_login')
def web_casos_sociales(request):
    user = request.user
    if not (
        user.is_superuser
        or user.rol in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPERVISOR)
        or user.es_funcionario_social
    ):
        raise PermissionDenied

    casos = CasoSocial.objects.all()
    if user.esta_limitado_a_delegacion or user.es_administrativo:
        # CU-28: el Funcionario social trabaja con los casos de su delegación.
        casos = casos.filter(delegacion_id=user.delegacion_id) if user.delegacion_id else casos.none()

    case_form = CasoSocialForm(user=user)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'crear_caso':
            case_form = CasoSocialForm(request.POST, user=user)
            if case_form.is_valid():
                with transaction.atomic():
                    caso = case_form.save()
                    RegistroAuditoria.objects.create(
                        usuario=user,
                        funcionario=user,
                        evento='CREACION',
                        accion='CREAR_CASO_SOCIAL',
                        entidad='CasoSocial',
                        identificador=str(caso.pk),
                        identificador_registro=str(caso.pk),
                        valor_anterior=None,
                        valor_nuevo={
                            'codigo_caso': caso.codigo_caso,
                            'delegacion_id': caso.delegacion_id,
                        },
                        ip_origen=obtener_ip_cliente(request) or None,
                    )
                messages.success(request, f'Caso social {caso.codigo_caso} creado.')
                return redirect('web_casos_sociales')
            messages.error(request, 'Revisa los datos del caso social.')
        elif action == 'registrar_atencion':
            caso = get_object_or_404(
                casos.select_related('delegacion'),
                pk=request.POST.get('caso_id'),
            )
            etapa = (
                caso.atenciones.order_by('-etapa')
                .values_list('etapa', flat=True)
                .first()
                or 0
            ) + 1
            form = AtencionSocialForm(
                request.POST,
                etapa=min(etapa, 3),
                area_id=user.area_entidad_id,
            )
            if form.is_valid():
                try:
                    with transaction.atomic():
                        atencion = caso.registrar_atencion(
                            funcionario=user,
                            tipo_atencion=form.cleaned_data[
                                'tipo_atencion_catalogo_id'
                            ].nombre,
                            detalle=form.cleaned_data['detalle'],
                            resultado=form.cleaned_data['resultado'],
                            fecha_gestion=form.cleaned_data['fecha_gestion'],
                            tipo_atencion_catalogo=form.cleaned_data[
                                'tipo_atencion_catalogo_id'
                            ],
                            subatencion_catalogo=form.cleaned_data[
                                'subatencion_catalogo_id'
                            ],
                        )
                        RegistroAuditoria.objects.create(
                            usuario=user,
                            funcionario=user,
                            evento='CREACION',
                            accion='REGISTRAR_ATENCION_SOCIAL',
                            entidad='AtencionSocial',
                            identificador=str(atencion.pk),
                            identificador_registro=str(atencion.pk),
                            valor_anterior=None,
                            valor_nuevo={
                                'caso_id': caso.pk,
                                'etapa': atencion.etapa,
                                'tipo_atencion': atencion.tipo_atencion,
                            },
                            ip_origen=obtener_ip_cliente(request) or None,
                        )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        f'Gestión de etapa {atencion.etapa} registrada en {caso.codigo_caso}.',
                    )
                    return redirect('web_casos_sociales')
            else:
                errores = [
                    error
                    for field_errors in form.errors.values()
                    for error in field_errors
                ]
                messages.error(
                    request,
                    '; '.join(errores) or 'Completa los datos de la gestión social.',
                )
        else:
            raise PermissionDenied

    casos = casos.prefetch_related('atenciones').select_related('delegacion')
    tipos_atencion = TipoAtencionSocial.objects.filter(
        activo=True,
        area__estado=True,
    )
    if user.area_entidad_id:
        tipos_atencion = tipos_atencion.filter(area_id=user.area_entidad_id)
    tipos_atencion = tipos_atencion.order_by('nombre')
    subatenciones = SubatencionSocial.objects.filter(
        activo=True,
        tipo_atencion__in=tipos_atencion,
    ).select_related('tipo_atencion').order_by('nombre')
    return render(request, 'casos_sociales.html', {
        'casos': casos,
        'case_form': case_form,
        'tipos_atencion': tipos_atencion,
        'subatenciones': subatenciones,
        'puede_crear_casos': True,
        'hoy': timezone.localdate(),
    })

def web_auditoria(request):
    if not request.user.is_authenticated or not (
        request.user.es_admin() or request.user.es_supervisor
    ):
        messages.warning(request, "Acceso restringido: El registro inmutable de auditoría está reservado para la Coordinación Comunal y Administradores (Seguridad RNF-008).")
        return redirect('web_dashboard')

    registros = RegistroAuditoria.objects.all().select_related(
        'usuario',
        'funcionario',
    )
    entidad = request.GET.get('entidad', '').strip()
    accion = request.GET.get('accion', '').strip()
    funcionario_id = request.GET.get('funcionario', '').strip()
    termino = request.GET.get('q', '').strip()
    fecha_desde_texto = request.GET.get('fecha_desde', '').strip()
    fecha_hasta_texto = request.GET.get('fecha_hasta', '').strip()
    fecha_desde = parse_date(fecha_desde_texto) if fecha_desde_texto else None
    fecha_hasta = parse_date(fecha_hasta_texto) if fecha_hasta_texto else None
    filtro_invalido = False
    if fecha_desde_texto and not fecha_desde:
        messages.error(request, 'La fecha inicial del filtro no es válida.')
        filtro_invalido = True
    if fecha_hasta_texto and not fecha_hasta:
        messages.error(request, 'La fecha final del filtro no es válida.')
        filtro_invalido = True
    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        messages.error(request, 'La fecha inicial no puede ser posterior a la fecha final.')
        filtro_invalido = True
    if entidad:
        registros = registros.filter(entidad__iexact=entidad)
    if accion:
        registros = registros.filter(accion=accion)
    if funcionario_id.isdecimal():
        registros = registros.filter(
            Q(funcionario_id=int(funcionario_id))
            | Q(funcionario__isnull=True, usuario_id=int(funcionario_id))
        )
    if termino:
        registros = registros.filter(
            Q(entidad__icontains=termino)
            | Q(accion__icontains=termino)
            | Q(identificador_registro__icontains=termino)
            | Q(funcionario__username__icontains=termino)
            | Q(funcionario__first_name__icontains=termino)
            | Q(funcionario__last_name__icontains=termino)
            | Q(usuario__username__icontains=termino)
            | Q(usuario__first_name__icontains=termino)
            | Q(usuario__last_name__icontains=termino)
        )
    if fecha_desde:
        registros = registros.filter(fecha_auditoria__date__gte=fecha_desde)
    if fecha_hasta:
        registros = registros.filter(fecha_auditoria__date__lte=fecha_hasta)
    if filtro_invalido:
        registros = registros.none()
    pagina = Paginator(registros.order_by('-fecha_auditoria', '-pk'), 50).get_page(
        request.GET.get('pagina')
    )
    return render(request, 'auditoria.html', {
        'registros': pagina.object_list,
        'pagina': pagina,
        'filtros_auditoria': request.GET.copy(),
        'filtro_entidad': entidad,
        'filtro_accion': accion,
        'filtro_funcionario': funcionario_id,
        'filtro_q': termino,
        'filtro_fecha_desde': fecha_desde_texto,
        'filtro_fecha_hasta': fecha_hasta_texto,
        'entidades_auditoria': (
            RegistroAuditoria.objects.order_by()
            .values_list('entidad', flat=True)
            .distinct()
        ),
        'acciones_auditoria': (
            RegistroAuditoria.objects.order_by()
            .values_list('accion', flat=True)
            .distinct()
        ),
        'usuarios_auditoria': Usuario.objects.filter(
            Q(auditorias__isnull=False) | Q(auditorias_legacy__isnull=False)
        ).distinct().order_by('last_name', 'first_name', 'username'),
    })

def api_geocodificar_direccion(request):
    import urllib.request, urllib.parse, json, re
    from urllib.error import URLError
    from django.http import JsonResponse

    latitud = request.GET.get('lat')
    longitud = request.GET.get('lng')
    if latitud is not None or longitud is not None:
        try:
            lat = float(latitud)
            lng = float(longitud)
        except (TypeError, ValueError):
            return JsonResponse({'encontrado': False, 'error': 'Coordenadas inválidas'}, status=400)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return JsonResponse({'encontrado': False, 'error': 'Coordenadas fuera de rango'}, status=400)

        url = (
            'https://nominatim.openstreetmap.org/reverse?format=jsonv2'
            f'&lat={lat}&lon={lng}&zoom=18&addressdetails=1'
        )
        headers = {'User-Agent': 'MatrizSGR-MunicipalidadLaSerena/1.0 (contacto@laserena.cl)'}
        try:
            req = urllib.request.Request(url, headers=headers)
            # URL https fija hacia Nominatim; lat/lng ya validados como float.
            with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
                data = json.loads(resp.read().decode('utf-8'))
        except (URLError, TimeoutError, json.JSONDecodeError):
            return JsonResponse(
                {'encontrado': False, 'error': 'No fue posible consultar la dirección cercana'},
                status=502,
            )

        if not isinstance(data, dict):
            return JsonResponse(
                {'encontrado': False, 'error': 'La respuesta de direcciones no es válida'},
                status=502,
            )
        address = data.get('address')
        if not isinstance(address, dict):
            address = {}
        calle = address.get('road') or address.get('pedestrian') or address.get('residential')
        numero = address.get('house_number')
        localidad = (
            address.get('city')
            or address.get('town')
            or address.get('village')
            or address.get('municipality')
        )
        sector = address.get('suburb') or address.get('neighbourhood')
        partes = []
        if calle:
            partes.append(f'{calle} {numero}' if numero else calle)
        if sector and sector.lower() not in (calle or '').lower():
            partes.append(sector)
        if localidad and localidad.lower() not in ' '.join(partes).lower():
            partes.append(localidad)
        direccion = ', '.join(partes) or data.get('display_name')
        if not direccion:
            return JsonResponse(
                {'encontrado': False, 'error': 'No se encontró una dirección cercana'},
                status=200,
            )
        return JsonResponse({
            'encontrado': True,
            'direccion_encontrada': direccion,
            'numero_encontrado': bool(numero),
            'aproximada': True,
            'lat': lat,
            'lng': lng,
        })

    query = (request.GET.get('q') or '').strip()
    if not query:
        return JsonResponse({'encontrado': False, 'error': 'Dirección no provista'}, status=400)

    headers = {'User-Agent': 'MatrizSGR-MunicipalidadLaSerena/1.0 (contacto@laserena.cl)'}
    clean_no_digits = re.sub(r'\d+', '', query).strip()

    attempts = [
        f'{query}, La Serena, Chile',
        f'{query}, Región de Coquimbo, Chile',
        f'{query}, Chile',
    ]
    if clean_no_digits and clean_no_digits != query:
        attempts.extend([
            f'{clean_no_digits}, La Serena, Chile',
            f'{clean_no_digits}, Región de Coquimbo, Chile',
            f'{clean_no_digits}, Chile',
        ])

    for q_att in attempts:
        url = f'https://nominatim.openstreetmap.org/search?format=json&limit=1&q={urllib.parse.quote(q_att)}'
        try:
            req = urllib.request.Request(url, headers=headers)
            # URL https fija hacia Nominatim; la consulta va escapada con quote().
            resp = urllib.request.urlopen(req, timeout=3)  # nosec B310
            data = json.loads(resp.read().decode('utf-8'))
            if data and len(data) > 0:
                lat = float(data[0]['lat'])
                lng = float(data[0]['lon'])
                display_name = data[0].get('display_name', query)
                return JsonResponse({
                    'encontrado': True,
                    'lat': lat,
                    'lng': lng,
                    'direccion_encontrada': display_name
                })
        except Exception:
            continue

    return JsonResponse({'encontrado': False, 'error': 'No se encontraron coordenadas exactas'}, status=200)


@login_required(login_url='web_login')
def web_brechas_historicas(request):
    """CU-17 (HU-22): compara la brecha por cargo entre períodos cerrados.

    Cada período se calcula con su propia configuración congelada al cierre
    (RF-038). El informe es descriptivo: no toma decisiones de recursos humanos.
    """
    if not (request.user.es_admin() or request.user.es_supervisor):
        raise PermissionDenied
    cerrados = PeriodoMedicion.objects.filter(cerrado=True).order_by('-fecha_termino', '-pk')
    seleccion = [p for p in request.GET.getlist('periodos') if p.isdecimal()]
    periodos = list(
        cerrados.filter(pk__in=seleccion) if seleccion else cerrados[:4]
    )
    periodos.sort(key=lambda periodo: periodo.fecha_inicio)
    funcionarios = Usuario.objects.filter(
        is_active=True,
        rol=Usuario.Rol.ADMINISTRATIVO,
        cargo__isnull=False,
    ).select_related('cargo')
    delegacion_id = request.GET.get('delegacion', '')
    if delegacion_id.isdecimal():
        funcionarios = funcionarios.filter(delegacion_id=int(delegacion_id))

    filas = {}
    for funcionario in funcionarios:
        fila = filas.setdefault(funcionario.cargo_id, {
            'cargo': funcionario.cargo,
            'celdas': {periodo.pk: [] for periodo in periodos},
        })
        for periodo in periodos:
            metricas = calcular_metricas_funcionario(funcionario, periodo, periodo.fecha_termino)
            if metricas['items']:
                fila['celdas'][periodo.pk].append(metricas['total_ponderado_pct'])

    tabla = []
    for fila in sorted(filas.values(), key=lambda f: f['cargo'].nombre):
        celdas = []
        for periodo in periodos:
            valores = fila['celdas'][periodo.pk]
            if valores:
                avance = round(sum(valores) / len(valores), 1)
                celdas.append({
                    'periodo': periodo,
                    'avance': avance,
                    'brecha': round(avance - 100, 1),
                    'funcionarios': len(valores),
                })
            else:
                celdas.append({'periodo': periodo, 'avance': None})
        con_datos = [c for c in celdas if c['avance'] is not None]
        tendencia = None
        if len(con_datos) >= 2:
            tendencia = round(con_datos[-1]['avance'] - con_datos[0]['avance'], 1)
        tabla.append({'cargo': fila['cargo'], 'celdas': celdas, 'tendencia': tendencia})

    return render(request, 'brechas_historicas.html', {
        'periodos_cerrados': cerrados,
        'periodos': periodos,
        'seleccion': [str(p.pk) for p in periodos],
        'delegaciones': Delegacion.objects.filter(activo=True).order_by('nombre'),
        'delegacion_id': delegacion_id,
        'tabla': tabla,
    })
