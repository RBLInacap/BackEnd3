"""
Módulo de Modelos de Datos - Matriz SGR (Ilustre Municipalidad de La Serena)
=============================================================================
Este módulo define la estructura de datos relacional y los métodos del modelo
de dominio y diagrama de clases UML (según especificación 'AjustesModelos.docx'
y 'MÉTODOS DIAGRAMA DE CLASES.docx' de Soledad Valdebenito) para la gestión
operativa, medición de cumplimiento territorial, evidencias fotográficas, casos
sociales, incentivos y registro inmutable de auditoría.

Soporta nomenclatura dual (snake_case y camelCase) para alineación 100% con los
diagramas de clases y el estándar PEP-8 de Django.
"""

import logging
import secrets
import string
import hashlib
import re
import time
from datetime import date
from django.db import IntegrityError, connections, models, router, transaction
from django.db.utils import OperationalError
from django.contrib.auth.models import AbstractUser, UserManager
from django.utils import timezone
from django.core.exceptions import ValidationError
from sgr_app.validators import (
    normalize_chilean_phone,
    normalize_chilean_rut,
    validate_chilean_phone,
    validate_chilean_rut,
)
from sgr_app.services.evidencias import metadatos_archivo_evidencia


def generar_codigo_evidencia(prefijo="SGR"):
    """
    Genera un código alfanumérico único para evidencias (Regla RN-010 y Criterio CA-05).
    Excluye caracteres ambiguos ('O', 'I', '0', '1') para facilitar lectura en terreno.
    Formato: [PREFIJO][6_ALFANUMERICOS] (ej: CIA9X7B2).
    """
    chars = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '').replace('1', '')
    aleatorio = ''.join(secrets.choice(chars) for _ in range(6))
    return f"{prefijo}{aleatorio}"


def generar_codigo_cargo():
    chars = (
        string.ascii_uppercase.replace('O', '').replace('I', '')
        + string.digits.replace('0', '').replace('1', '')
    )
    aleatorio = ''.join(secrets.choice(chars) for _ in range(8))
    return f'CAR-{aleatorio}'


PERMISOS_ROL = (
    ('ACCESO_ADMINISTRADOR', 'Acceso de administrador'),
    ('GESTIONAR_COORDINACION', 'Gestionar coordinación'),
    ('GESTIONAR_DELEGACION', 'Gestionar delegación'),
    ('CREAR_META', 'Crear metas'),
    ('APROBAR_EVIDENCIA', 'Aprobar evidencias'),
    ('REGISTRAR_ACTIVIDAD', 'Registrar actividades'),
    ('VER_DASHBOARD', 'Ver paneles'),
)

PERMISOS_PREDETERMINADOS_POR_ROL = {
    'SUPERVISOR': (
        'CREAR_META',
        'APROBAR_EVIDENCIA',
        'VER_DASHBOARD',
        'GESTIONAR_COORDINACION',
    ),
    'ADMINISTRATIVO': ('REGISTRAR_ACTIVIDAD', 'VER_DASHBOARD'),
    # CU-38: perfil de solo lectura de tableros e informes de su ámbito.
    'CONSULTA': ('VER_DASHBOARD',),
}


# =============================================================================
# 7. CLASE ROL
# =============================================================================
class Rol(models.Model):
    """
    Entidad de Roles y Privilegios en el modelo de dominio UML (Clase Rol).
    """
    nombre_rol = models.CharField(max_length=50, unique=True, verbose_name="Nombre del Rol")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción de Permisos")
    estado = models.BooleanField(default=True, verbose_name="Estado Activo")
    permisos = models.JSONField(default=list, blank=True, verbose_name="Permisos")

    class Meta:
        db_table = 'Roles'
        verbose_name = "Rol"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.nombre_rol

    @property
    def permisos_efectivos(self):
        if self.nombre_rol.upper() == 'ADMINISTRADOR':
            return tuple(codigo for codigo, _ in PERMISOS_ROL)
        return tuple(sorted(
            set(PERMISOS_PREDETERMINADOS_POR_ROL.get(self.nombre_rol.upper(), ()))
            | set(self.permisos or ())
        ))

    @property
    def nombre(self):
        return self.nombre_rol

    @nombre.setter
    def nombre(self, val):
        self.nombre_rol = val

    def obtenerCantidadUsuarios(self) -> int:
        """Retorna la cantidad total de funcionarios activos asociados a este rol."""
        if hasattr(self, 'usuarios'):
            return self.usuarios.filter(is_active=True).distinct().count()
        return Usuario.objects.filter(
            rol=self.nombre_rol.upper(),
            is_active=True,
        ).count()

    def tienePermiso(self, codigo_permiso: str) -> bool:
        """Evalúa si el rol posee determinado permiso para autorizar una acción."""
        if not self.estado:
            return False
        nombre_rol = self.nombre_rol.upper()
        if nombre_rol == "ADMINISTRADOR":
            return True
        permisos_asignados = set(
            PERMISOS_PREDETERMINADOS_POR_ROL.get(nombre_rol, ())
        )
        permisos_asignados.update(self.permisos or ())
        return codigo_permiso.upper() in permisos_asignados


# =============================================================================
# 10. CLASE AREA
# =============================================================================
class Area(models.Model):
    """
    Área funcional municipal en el modelo de dominio UML (Clase Area).
    """
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    estado = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Área"
        verbose_name_plural = "Áreas"

    def __str__(self):
        return self.nombre

    def obtenerCantidadFuncionarios(self) -> int:
        """Retorna la cantidad de funcionarios activos asociados al área."""
        return self.usuarios_area.filter(is_active=True).count() if hasattr(self, 'usuarios_area') else 0

    def calcularCumplimientoArea(self) -> float:
        """Calcula el porcentaje de avance promedio de las metas de todos los funcionarios pertenecientes a esta área."""
        funcionarios = self.usuarios_area.filter(is_active=True) if hasattr(self, 'usuarios_area') else []
        if not funcionarios:
            return 0.0
        total_avance = 0.0
        conteo_metas = 0
        for f in funcionarios:
            metas = f.metas.all() if hasattr(f, 'metas') else []
            for m in metas:
                total_avance += m.calcular_meta() if hasattr(m, 'calcular_meta') else 0.0
                conteo_metas += 1
        if conteo_metas == 0:
            return 0.0
        return round(total_avance / conteo_metas, 2)

    def activarArea(self) -> bool:
        """Activa el área funcional."""
        self.estado = True
        self.save(update_fields=['estado'])
        return True

    def desactivarArea(self) -> bool:
        """Desactiva el área sin borrar registros históricos."""
        self.estado = False
        self.save(update_fields=['estado'])
        return True


# =============================================================================
# 8. CLASE DELEGACION
# =============================================================================
class Delegacion(models.Model):
    """
    Representa una de las 6 delegaciones municipales territoriales de La Serena (RF-003).
    (Las Compañías, La Antena-La Florida, Centro, Avenida del Mar, Rural, La Pampa).
    """
    nombre = models.CharField(max_length=100, unique=True, verbose_name="Nombre Delegación")
    codigo = models.CharField(max_length=10, unique=True)
    direccion = models.CharField(max_length=255, blank=True, null=True, verbose_name="Dirección Física")
    telefono = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    activo = models.BooleanField(default=True, verbose_name="Estado Activo")
    ambito = models.CharField(max_length=100, default="Urbano", verbose_name="Ámbito Territorio")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Delegación"
        verbose_name_plural = "Delegaciones"
        ordering = ['nombre']

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"

    @property
    def estado(self):
        return self.activo

    @estado.setter
    def estado(self, val):
        self.activo = val

    def activar_delegacion(self) -> bool:
        self.activo = True
        self.save(update_fields=['activo'])
        return True

    activarDelegacion = activar_delegacion

    def desactivar_delegacion(self) -> bool:
        self.activo = False
        self.save(update_fields=['activo'])
        return True

    desactivarDelegacion = desactivar_delegacion

    def calcularCumplimientoGlobal(self) -> float:
        """Calcula el porcentaje de cumplimiento promediando el avance de todas las metas asociadas."""
        metas = self.metas.all() if hasattr(self, 'metas') and self.metas.exists() else []
        if not metas:
            from sgr_app.services.calculos import calcular_resumen_delegacion
            periodo = PeriodoMedicion.objects.filter(cerrado=False).first() or PeriodoMedicion.objects.first()
            if periodo:
                return calcular_resumen_delegacion(self, periodo).get('promedio_ponderado_pct', 0.0)
            return 0.0
        total_avance = sum(m.calcular_meta() for m in metas)
        return round(total_avance / len(metas), 2)

    def obtenerResumenSemaforo(self) -> dict:
        """Clasifica las metas de la delegación en niveles de semáforo (Verde, Amarillo, Rojo)."""
        metas = self.metas.all() if hasattr(self, 'metas') and self.metas.exists() else []
        resumen = {"VERDE": 0, "AMARILLO": 0, "ROJO": 0, "TOTAL": 0}
        if not metas:
            from sgr_app.services.calculos import calcular_resumen_delegacion
            periodo = PeriodoMedicion.objects.filter(cerrado=False).first() or PeriodoMedicion.objects.first()
            if periodo:
                sem = calcular_resumen_delegacion(self, periodo).get('semaforo_delegacion', 'ROJO')
                if sem == 'AMBAR':
                    sem = 'AMARILLO'
                resumen[sem] = 1
                resumen["TOTAL"] = 1
            return resumen
        # RNF-015: umbrales configurados en el período de cada meta.
        from sgr_app.services.calculos import calcular_dias_periodo, determinar_color_semaforo
        dias_por_periodo = {}
        for meta in metas:
            if meta.periodo_id not in dias_por_periodo:
                dias_por_periodo[meta.periodo_id] = calcular_dias_periodo(meta.periodo)
            dias = dias_por_periodo[meta.periodo_id]
            color = determinar_color_semaforo(
                meta.calcular_meta(),
                dias['meta_esperada_pct'],
                umbral_verde=dias['umbral_verde_ratio'],
                umbral_ambar=dias['umbral_ambar_ratio'],
            )
            resumen['AMARILLO' if color == 'AMBAR' else color] += 1
        resumen["TOTAL"] = len(metas)
        return resumen


# =============================================================================
# 9. CLASE CARGO
# =============================================================================
class Cargo(models.Model):
    codigo = models.CharField(
        max_length=12,
        unique=True,
        default=generar_codigo_cargo,
        editable=False,
        verbose_name='Código',
    )
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)
    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cargos',
        verbose_name="Área",
    )

    class Meta:
        verbose_name = "Cargo"
        verbose_name_plural = "Cargos"

    def __str__(self):
        return self.nombre

    @property
    def estado(self):
        return self.activo

    @estado.setter
    def estado(self, val):
        self.activo = val

    @property
    def nombre_cargo(self):
        return self.nombre

    @property
    def nombreCargo(self):
        return self.nombre

    def obtenerCantidadFuncionarios(self) -> int:
        """Retorna la cantidad total de funcionarios activos asociados a este cargo."""
        return self.usuarios.filter(is_active=True).count()

    def activarCargo(self) -> bool:
        """Activa el puesto laboral en el sistema."""
        self.activo = True
        self.save(update_fields=['activo'])
        return True

    def desactivarCargo(self) -> bool:
        """Desactiva el puesto laboral sin eliminar su historial."""
        self.activo = False
        self.save(update_fields=['activo'])
        return True


# =============================================================================
# 5. CLASE FUNCIONARIO / USUARIO
# =============================================================================
class UsuarioManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('rol', 'ADMINISTRADOR')
        return super().create_superuser(
            username,
            email=email,
            password=password,
            **extra_fields,
        )


class Usuario(AbstractUser):
    """
    Usuario institucional municipal con control de roles (RBAC) y soporte 2FA (RF-001, RF-002, RNF-008).
    Roles: Administrador, Supervisor y Administrativo. Delegado es un perfil territorial
    aplicado a usuarios con rol Supervisor.
    """
    class Rol(models.TextChoices):
        ADMINISTRADOR = 'ADMINISTRADOR', 'Administrador'
        SUPERVISOR = 'SUPERVISOR', 'Supervisor'
        ADMINISTRATIVO = 'ADMINISTRATIVO', 'Administrativo'
        CONSULTA = 'CONSULTA', 'Usuario de consulta'

    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.ADMINISTRATIVO)
    es_delegado = models.BooleanField(
        default=False,
        verbose_name="Perfil Delegado",
        help_text="Limita el acceso del Supervisor a los datos de su delegación.",
    )
    roles = models.ManyToManyField(
        'Rol',
        blank=True,
        related_name='usuarios',
        verbose_name='Roles adicionales',
    )
    area_entidad = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios_area')
    cargo = models.ForeignKey(Cargo, on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios')
    delegacion = models.ForeignKey(Delegacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios')
    telefono = models.CharField(max_length=30, blank=True, null=True, validators=[validate_chilean_phone])
    rut_anonimizado = models.CharField(max_length=20, blank=True, null=True, validators=[validate_chilean_rut])
    totp_secret = models.CharField(max_length=64, blank=True, null=True, help_text="Preparado para 2FA")
    requires_2fa = models.BooleanField(default=False)
    is_2fa_verified = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    initial_login_expires_at = models.DateTimeField(null=True, blank=True)
    objects = UsuarioManager()

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rol__in=[
                    'ADMINISTRADOR',
                    'SUPERVISOR',
                    'ADMINISTRATIVO',
                    'CONSULTA',
                ]),
                name='usuario_rol_valido',
            ),
            models.CheckConstraint(
                condition=models.Q(es_delegado=False) | models.Q(rol='SUPERVISOR'),
                name='delegado_debe_ser_supervisor',
            ),
        ]

    def __str__(self):
        return f"{self.get_full_name() or self.username} [{self.get_rol_display()}]"

    @property
    def estado(self):
        return self.is_active

    @estado.setter
    def estado(self, val):
        self.is_active = val

    @property
    def run(self):
        return self.rut_anonimizado

    @run.setter
    def run(self, val):
        self.rut_anonimizado = val

    @property
    def correo(self):
        return self.email

    @correo.setter
    def correo(self, val):
        self.email = val

    @property
    def nombre(self):
        return self.first_name

    @nombre.setter
    def nombre(self, val):
        self.first_name = val

    @property
    def apellido(self):
        return self.last_name

    @apellido.setter
    def apellido(self, val):
        self.last_name = val

    @property
    def apellidoPaterno(self):
        return self.last_name

    def tiene_permiso(self, codigo_permiso: str) -> bool:
        """Comprueba permisos concedidos por el rol principal o cualquier rol adicional."""
        codigo_permiso = codigo_permiso.upper()
        if self.is_superuser or self.rol == self.Rol.ADMINISTRADOR:
            return True
        if codigo_permiso in PERMISOS_PREDETERMINADOS_POR_ROL.get(self.rol, ()):
            return True
        if self.pk:
            return any(
                rol.tienePermiso(codigo_permiso)
                for rol in self.roles.filter(estado=True)
            )
        return False

    def es_admin(self) -> bool:
        """Verifica si el funcionario cuenta con privilegios administrativos."""
        return self.tiene_permiso('ACCESO_ADMINISTRADOR')

    @property
    def es_supervisor(self) -> bool:
        return (
            self.tiene_permiso('GESTIONAR_COORDINACION')
            and not self.es_delegado
        )

    @property
    def es_consulta(self) -> bool:
        """CU-38: usuario de solo lectura (tableros e informes de su ámbito)."""
        return self.rol == self.Rol.CONSULTA and not self.is_superuser

    @property
    def es_funcionario_social(self) -> bool:
        """CU-28 (HU-03): Funcionario de un área con tipos de atención social activos."""
        return bool(
            self.es_administrativo
            and self.area_entidad_id
            and TipoAtencionSocial.objects.filter(
                area_id=self.area_entidad_id,
                activo=True,
            ).exists()
        )

    @property
    def es_administrativo(self) -> bool:
        return (
            self.rol == self.Rol.ADMINISTRATIVO
            and not self.es_admin()
            and not self.es_supervisor
            and not self.esta_limitado_a_delegacion
        )

    def es_coordinador_o_delegado(self) -> bool:
        """Determina si el funcionario tiene perfil de supervisión territorial."""
        return (
            self.es_delegado
            or self.tiene_permiso('GESTIONAR_COORDINACION')
            or self.tiene_permiso('GESTIONAR_DELEGACION')
        )

    @property
    def esta_limitado_a_delegacion(self) -> bool:
        if self.rol == self.Rol.CONSULTA:
            return bool(self.delegacion_id)
        return self.es_delegado or (
            self.tiene_permiso('GESTIONAR_DELEGACION')
            and not self.tiene_permiso('GESTIONAR_COORDINACION')
            and not self.es_admin()
        )

    @property
    def puede_aprobar_evidencias(self) -> bool:
        return self.tiene_permiso('APROBAR_EVIDENCIA')

    def puede_aprobar_ajustes(self) -> bool:
        """Consulta si el rol asociado al funcionario permite aprobar ajustes de incentivo."""
        return self.es_admin() or self.es_supervisor

    def validarRun(self) -> bool:
        if not self.rut_anonimizado:
            return False
        try:
            validate_chilean_rut(self.rut_anonimizado)
            return True
        except ValidationError:
            return False

    def save(self, *args, **kwargs):
        if self.rol not in self.Rol.values:
            raise ValidationError({'rol': 'Selecciona un rol institucional válido.'})
        if self.es_delegado and (
            self.rol != self.Rol.SUPERVISOR or not self.delegacion_id
        ):
            raise ValidationError({
                'es_delegado': 'El perfil Delegado requiere el rol Supervisor y una delegación asignada.',
            })
        if self.rut_anonimizado:
            validate_chilean_rut(self.rut_anonimizado)
            self.rut_anonimizado = normalize_chilean_rut(self.rut_anonimizado)
        if self.telefono:
            self.telefono = normalize_chilean_phone(self.telefono)
        super().save(*args, **kwargs)

    def registrar_actividad(self, fecha_solicitud=None, solicitud_problema="", accion="", item=None, solicitante=None):
        """Crea y asocia una nueva actividad transaccional al funcionario."""
        if not fecha_solicitud:
            fecha_solicitud = timezone.localdate()
        actividad = Actividad.objects.create(
            funcionario=self,
            delegacion=self.delegacion or Delegacion.objects.first(),
            fecha_actividad=fecha_solicitud,
            solicitud_problema=solicitud_problema,
            actividad_solicitud=solicitud_problema or "Actividad en terreno",
            accion=accion,
            accion_ejecutada=accion or "Gestión realizada",
            item=item,
            item_medicion=item,
            solicitante=solicitante if isinstance(solicitante, Solicitante) else None,
            contacto_nombre=str(solicitante) if solicitante else ""
        )
        return actividad

    registrarActividad = registrar_actividad

    def activar_usuario(self) -> bool:
        """Activa la cuenta del usuario en el sistema."""
        self.is_active = True
        self.save(update_fields=['is_active'])
        return True

    activarUsuario = activar_usuario

    def desactivar_usuario(self) -> bool:
        """Desactiva la cuenta del usuario sin eliminar historial de auditoría ni evidencias (Baja lógica)."""
        self.is_active = False
        self.save(update_fields=['is_active'])
        return True

    desactivarUsuario = desactivar_usuario

    def asignar_rol(self, nuevo_rol: str) -> bool:
        """Asigna un nuevo rol de sistema al usuario."""
        if nuevo_rol in Usuario.Rol.values:
            self.rol = nuevo_rol
            if nuevo_rol != Usuario.Rol.SUPERVISOR:
                self.es_delegado = False
                self.save(update_fields=['rol', 'es_delegado'])
            else:
                self.save(update_fields=['rol'])
            return True
        return False

    asignarRol = asignar_rol

    def asignar_delegacion(self, nueva_delegacion) -> bool:
        """Asigna o transfiere al usuario a una delegación municipal."""
        self.delegacion = nueva_delegacion
        self.save(update_fields=['delegacion'])
        return True

    asignarDelegacion = asignar_delegacion

    def validar_acceso(self) -> bool:
        """Verifica si el usuario está activo y no bloqueado."""
        return self.is_active

    validarAcceso = validar_acceso

    def obtenerCumplimientoGeneral(self) -> float:
        """Calcula el porcentaje de cumplimiento promediando el avance de sus metas asociadas."""
        metas = self.metas.all() if hasattr(self, 'metas') and self.metas.exists() else []
        if not metas:
            from sgr_app.services.calculos import calcular_metricas_funcionario
            periodo = PeriodoMedicion.objects.filter(cerrado=False).first() or PeriodoMedicion.objects.first()
            if periodo:
                return calcular_metricas_funcionario(self, periodo).get('total_ponderado_pct', 0.0)
            return 0.0
        total_porcentaje = sum(meta.calcular_meta() for meta in metas)
        return round(total_porcentaje / len(metas), 2)

    def ver_dashboard_personal(self) -> dict:
        """Devuelve las métricas clave del funcionario para su panel inicial."""
        actividades_mes = self.actividades.filter(
            fecha_registro__month=timezone.now().month
        ).count()
        return {
            "funcionario": f"{self.first_name} {self.last_name}",
            "actividades_realizadas_mes": actividades_mes,
            "cumplimiento_general": self.obtenerCumplimientoGeneral(),
            "estado_cuenta": "ACTIVO" if self.is_active else "INACTIVO"
        }

    verDashboardPersonal = ver_dashboard_personal


class CodigoRecuperacionVigenteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(invalidado_en__isnull=True)


class PasswordResetCode(models.Model):
    # Por normativa los códigos no se borran: al usarse o reemplazarse se invalidan
    # y quedan como historial (`todos`). `objects` entrega solo el código vigente.
    user = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        related_name='codigos_recuperacion',
    )
    code_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    invalidado_en = models.DateTimeField(null=True, blank=True)
    motivo_invalidacion = models.CharField(max_length=20, blank=True, default='')

    objects = CodigoRecuperacionVigenteManager()
    todos = models.Manager()

    class Meta:
        verbose_name = 'Código de recuperación de contraseña'
        verbose_name_plural = 'Códigos de recuperación de contraseña'
        base_manager_name = 'todos'
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(invalidado_en__isnull=True),
                name='codigo_recuperacion_vigente_unico',
            ),
        ]

    def invalidar(self, motivo):
        self.invalidado_en = timezone.now()
        self.motivo_invalidacion = motivo
        self.save(update_fields=['invalidado_en', 'motivo_invalidacion'])


class EjecucionTarea(models.Model):
    """Historial de tareas automáticas: respaldo, purga, monitoreo (RNF-018)."""
    nombre = models.CharField(max_length=80, db_index=True)
    inicio = models.DateTimeField(default=timezone.now)
    fin = models.DateTimeField(null=True, blank=True)
    exito = models.BooleanField(null=True)
    detalle = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-inicio']
        verbose_name = 'Ejecución de tarea'
        verbose_name_plural = 'Ejecuciones de tareas'

    def __str__(self):
        estado = 'en curso' if self.exito is None else ('OK' if self.exito else 'FALLÓ')
        return f'{self.nombre} {self.inicio:%Y-%m-%d %H:%M} {estado}'


class Indisponibilidad(models.Model):
    """Periodo en que el sistema no estuvo disponible (RNF-001)."""

    class Tipo(models.TextChoices):
        CAIDA = 'CAIDA', 'Caída detectada por el monitor'
        SIN_MONITOREO = 'SIN_MONITOREO', 'Servidor apagado o monitor detenido'
        MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento planificado'

    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.CAIDA)
    causa = models.TextField(blank=True, default='')
    registrado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-inicio']
        verbose_name = 'Indisponibilidad'
        verbose_name_plural = 'Indisponibilidades'

    @property
    def duracion(self):
        return self.fin - self.inicio


class BloqueoAcceso(models.Model):
    """
    Contador de intentos fallidos de autenticación (OWASP A07).

    La clave es un hash SHA-256 del ámbito (usuario+IP, IP o 2FA), por lo que
    no se almacenan nombres de usuario ni direcciones IP en texto plano.
    """
    clave = models.CharField(max_length=64, unique=True)
    intentos_fallidos = models.PositiveIntegerField(default=0)
    primer_fallo = models.DateTimeField(default=timezone.now)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Bloqueo de acceso'
        verbose_name_plural = 'Bloqueos de acceso'


# =============================================================================
# 4. CLASE SOLICITANTE
# =============================================================================
class Solicitante(models.Model):
    """
    Persona o entidad requirente de intervenciones en terreno (Clase Solicitante).
    """
    TIPO_SOLICITANTE_CHOICES = [
        ('VECINO', 'Vecino / Particular'),
        ('DIRIGENTE', 'Dirigente Social'),
        ('INSTITUCION', 'Junta de Vecinos / Organización'),
        ('INTERNO', 'Unidad Interna Municipal'),
    ]

    run = models.CharField(max_length=20, unique=True, null=True, blank=True, validators=[validate_chilean_rut])
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    apellido = models.CharField(max_length=100, default='', verbose_name="Apellido")
    apellido_paterno = models.CharField(max_length=100, blank=True, default='', verbose_name="Apellido Paterno")
    apellido_materno = models.CharField(max_length=100, blank=True, default='', verbose_name="Apellido Materno")
    tipo_solicitante = models.CharField(max_length=30, choices=TIPO_SOLICITANTE_CHOICES, default='VECINO', verbose_name="Tipo de Solicitante")
    telefono = models.CharField(max_length=15, blank=True, null=True, validators=[validate_chilean_phone], verbose_name="Teléfono de Contacto")
    correo = models.EmailField(blank=True, null=True, verbose_name="Correo Electrónico")
    direccion = models.CharField(max_length=255, blank=True, null=True)
    estado = models.BooleanField(default=True, verbose_name="Activo")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'Solicitantes'
        verbose_name = "Solicitante"
        verbose_name_plural = "Solicitantes"

    def __str__(self):
        return f"{self.nombre} {self.apellido}".strip() or f"Solicitante #{self.id}"

    @property
    def tipoSolicitante(self):
        return self.tipo_solicitante

    @tipoSolicitante.setter
    def tipoSolicitante(self, val):
        self.tipo_solicitante = val

    def validarRun(self) -> bool:
        if not self.run:
            return False
        try:
            validate_chilean_rut(self.run)
            return True
        except ValidationError:
            return False

    def save(self, *args, **kwargs):
        if self.run:
            validate_chilean_rut(self.run)
            self.run = normalize_chilean_rut(self.run)
        if self.telefono:
            self.telefono = normalize_chilean_phone(self.telefono)
        super().save(*args, **kwargs)

    def obtener_historial(self):
        """Devuelve todas las actividades vinculadas a este solicitante."""
        if hasattr(self, 'actividades') and self.actividades.exists():
            return self.actividades.all().order_by('-fecha_actividad')
        return Actividad.objects.filter(contacto_nombre__icontains=self.nombre).order_by('-fecha_registro')

    obtenerHistorial = obtener_historial

    def crear_contacto(self):
        self.save()

    crearContacto = crear_contacto

    def modificar_contacto(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save()

    modificarContacto = modificar_contacto

    def eliminar_contacto(self):
        """Baja lógica: por normativa el solicitante no se borra, se desactiva."""
        self.estado = False
        self.save(update_fields=['estado'])

    eliminarContacto = eliminar_contacto


# =============================================================================
# 13. CLASE PERIODO / PERIODOMEDICION
# =============================================================================
class PeriodoMedicion(models.Model):
    """
    Período trimestral de evaluación con cálculo automático de días computables (RF-006, RN-007).
    """
    nombre = models.CharField(max_length=100, unique=True, help_text="Ej: Trimestre 3 - 2026")
    anio = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Año")
    trimestre = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4')],
        verbose_name="Trimestre",
    )
    fecha_inicio = models.DateField(default=date(2026, 7, 1), verbose_name='Fecha de Inicio')
    fecha_termino = models.DateField(default=date(2026, 9, 30), verbose_name='Fecha de Término')
    dias_computables = models.IntegerField(default=92, verbose_name='Días Computables')
    cerrado = models.BooleanField(default=False, verbose_name='Período Cerrado')
    estado = models.BooleanField(default=True, verbose_name="Activo")
    umbral_verde = models.FloatField(default=80.0, verbose_name='Umbral Verde (%)')
    umbral_ambar = models.FloatField(default=50.0, verbose_name='Umbral Ámbar (%)')
    umbral_semaforo_verde = models.FloatField(
        default=100.0,
        verbose_name='Semáforo verde: avance del objetivo (%)',
        help_text='Avance mínimo respecto a la meta proporcional esperada para clasificar en verde.',
    )
    umbral_semaforo_ambar = models.FloatField(
        default=60.0,
        verbose_name='Semáforo ámbar: avance del objetivo (%)',
        help_text='Avance mínimo respecto a la meta proporcional esperada para clasificar en ámbar.',
    )
    umbral_colectivo = models.FloatField(default=80.0, help_text="Umbral mínimo colectivo (%)")
    tope_cumplimiento = models.FloatField(default=150.0, verbose_name='Tope Cumplimiento (%)')
    version_de_parametro = models.CharField(max_length=50, default="1.0", verbose_name='Versión de Parámetro')
    version_cierre = models.CharField(max_length=50, blank=True, default='')
    cerrado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'Periodos'
        verbose_name = "Período"
        verbose_name_plural = "Períodos"
        ordering = ['-fecha_inicio']

    def clean(self):
        if self.fecha_termino and self.fecha_inicio and self.fecha_termino < self.fecha_inicio:
            raise ValidationError("La fecha de término no puede ser anterior a la fecha de inicio.")
        if self.umbral_semaforo_ambar < 0 or self.umbral_semaforo_verde < 0:
            raise ValidationError("Los umbrales del semáforo no pueden ser negativos.")
        if self.umbral_semaforo_ambar >= self.umbral_semaforo_verde:
            raise ValidationError("El umbral ámbar debe ser menor que el umbral verde.")
        if self.tope_cumplimiento <= 0 or self.umbral_colectivo < 0:
            raise ValidationError("El tope debe ser positivo y el umbral colectivo no puede ser negativo.")

    def save(self, *args, **kwargs):
        self.umbral_verde = self.umbral_semaforo_verde
        self.umbral_ambar = self.umbral_semaforo_ambar
        original = None
        if self.pk and not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).first()
            if original and original.cerrado:
                parameter_fields = (
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
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in parameter_fields
                ):
                    raise ValidationError(
                        'No se pueden modificar los parámetros de un período cerrado.'
                    )
        recien_cerrado = bool(self.cerrado and (not original or not original.cerrado))
        if recien_cerrado:
            self.version_cierre = self.version_de_parametro
            self.cerrado_en = timezone.now()
        elif not self.cerrado:
            self.version_cierre = ''
            self.cerrado_en = None
        if self.fecha_inicio and self.fecha_termino:
            self.dias_computables = max((self.fecha_termino - self.fecha_inicio).days + 1, 1)
            self.anio = self.fecha_inicio.year
            self.trimestre = (self.fecha_inicio.month - 1) // 3 + 1
        self.clean()
        update_fields = kwargs.get('update_fields')
        if update_fields:
            kwargs['update_fields'] = set(update_fields) | {
                'umbral_verde',
                'umbral_ambar',
                'version_cierre',
                'cerrado_en',
            }
        super().save(*args, **kwargs)
        if recien_cerrado:
            # RF-038: congela la configuración con la que se cierra el período.
            from sgr_app.services.configuracion import asegurar_version_cierre

            asegurar_version_cierre(self)

    def __str__(self):
        return f"{self.nombre} ({self.fecha_inicio} al {self.fecha_termino})"

    # Aliases de Atributos UML / Snake_case
    @property
    def inicio_periodo(self):
        return self.fecha_inicio

    @inicio_periodo.setter
    def inicio_periodo(self, val):
        self.fecha_inicio = val

    @property
    def termino_periodo(self):
        return self.fecha_termino

    @termino_periodo.setter
    def termino_periodo(self, val):
        self.fecha_termino = val

    @property
    def inicioPeriodo(self):
        return self.fecha_inicio

    @property
    def terminoPeriodo(self):
        return self.fecha_termino

    @property
    def diasComputables(self):
        return self.dias_computables

    @property
    def umbralVerde(self):
        return self.umbral_semaforo_verde

    @property
    def umbralAmbar(self):
        return self.umbral_semaforo_ambar

    @property
    def topeCumplimiento(self):
        return self.tope_cumplimiento

    @property
    def versionDeParametro(self):
        return self.version_de_parametro

    # Métodos UML / Snake_case
    def vigencia_fecha(self, fecha: date = None) -> bool:
        """Verifica si la fecha actual está dentro del rango del período."""
        if fecha is None:
            fecha = timezone.now().date()
        return self.fecha_inicio <= fecha <= self.fecha_termino

    vigenciaFecha = vigencia_fecha

    def estaActivo(self) -> bool:
        return not self.cerrado and self.vigencia_fecha()

    def calcular_dias_transcurridos(self) -> int:
        """Calcula cuántos días computables han transcurrido a la fecha de hoy."""
        hoy = timezone.now().date()
        if hoy < self.fecha_inicio:
            return 0
        if hoy > self.fecha_termino:
            return self.dias_computables
        return (hoy - self.fecha_inicio).days + 1

    calcularDiasTranscurridos = calcular_dias_transcurridos

    def calcular_meta_esperada_al_dia(self, valorMeta: float = None) -> float:
        """RN-007 / RF-026: Calcula el % acumulado esperado según los días transcurridos."""
        if self.dias_computables == 0:
            return 0.0
        dias_pasados = self.calcular_dias_transcurridos()
        porcentaje = (dias_pasados / self.dias_computables) * 100.0
        pct_esperado = min(porcentaje, 100.0)
        if valorMeta is not None:
            return round((valorMeta * pct_esperado) / 100.0, 2)
        return round(pct_esperado, 2)

    calcularMetaEsperadaAlDia = calcular_meta_esperada_al_dia

    def evaluar_cumplimiento(self, porcentaje_avance: float) -> str:
        """RN-008 / RF-027: Evalúa el porcentaje respecto al esperado y asigna el color."""
        meta_esperada = self.calcular_meta_esperada_al_dia()
        if meta_esperada <= 0:
            return 'VERDE' if porcentaje_avance >= 0 else 'ROJO'

        porcentaje_evaluado = (porcentaje_avance / meta_esperada) * 100.0

        if porcentaje_evaluado >= self.umbral_semaforo_verde:
            return 'VERDE'
        elif porcentaje_evaluado >= self.umbral_semaforo_ambar:
            return 'AMBAR'
        else:
            return 'ROJO'

    def evaluarCumplimiento(self, porcentaje: float) -> str:
        res = self.evaluar_cumplimiento(porcentaje)
        return "AMARILLO" if res == "AMBAR" else res

    def cerrar_periodo(self) -> bool:
        """RN-013: Bloquea el período impidiendo futuras modificaciones."""
        self.cerrado = True
        self.save(update_fields=['cerrado'])
        return True

    cerrarPeriodo = cerrar_periodo

    def reabrir_periodo(self) -> bool:
        """RN-013: Reabre un período cerrado bajo autorización y auditoría."""
        self.cerrado = False
        self.save(update_fields=['cerrado'])
        return True

    reabrirPeriodo = reabrir_periodo

    def calcular_meta_al_dia(self, fecha_corte=None) -> float:
        """RN-007: Calcula el porcentaje de avance esperado a la fecha de corte."""
        from sgr_app.services.calculos import calcular_dias_periodo
        return calcular_dias_periodo(self, fecha_corte)['meta_esperada_pct']

    calcularMetaAlDia = calcular_meta_al_dia

    def es_fecha_valida(self, fecha) -> bool:
        """Verifica si una fecha se encuentra dentro de los límites del período."""
        return self.fecha_inicio <= fecha <= self.fecha_termino

    esFechaValida = es_fecha_valida


# =============================================================================
# CATÁLOGO DE SERVICIOS
# =============================================================================
class CatalogoServicio(models.Model):
    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=150)
    area = models.CharField(max_length=100, blank=True, null=True)
    area_ref = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='servicios',
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Catálogo de Servicio"
        verbose_name_plural = "Catálogo de Servicios"

    def __str__(self):
        return f"[{self.codigo}] {self.nombre}"


class TipoActividad(models.Model):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='tipos_actividad')
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, default='')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['area__nombre', 'nombre']
        unique_together = ('area', 'nombre')
        verbose_name = 'Tipo de actividad'
        verbose_name_plural = 'Tipos de actividad'

    def __str__(self):
        return f'{self.nombre} ({self.area.nombre})'


class TipoAtencionSocial(models.Model):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='tipos_atencion_social')
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, default='')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['area__nombre', 'nombre']
        unique_together = ('area', 'nombre')
        verbose_name = 'Tipo de atención social'
        verbose_name_plural = 'Tipos de atención social'

    def __str__(self):
        return f'{self.nombre} ({self.area.nombre})'


class SubatencionSocial(models.Model):
    tipo_atencion = models.ForeignKey(
        TipoAtencionSocial,
        on_delete=models.PROTECT,
        related_name='subatenciones',
    )
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, default='')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['tipo_atencion__area__nombre', 'tipo_atencion__nombre', 'nombre']
        unique_together = ('tipo_atencion', 'nombre')
        verbose_name = 'Subatención social'
        verbose_name_plural = 'Subatenciones sociales'

    def __str__(self):
        return f'{self.nombre} ({self.tipo_atencion})'


# =============================================================================
# 11. CLASE ITEM / ITEMMEDICION
# =============================================================================
class ItemMedicion(models.Model):
    """
    Entidad que representa las prestaciones o bienes medibles (Clase Item).
    """
    TIPO_UNIDAD_CHOICES = [
        ('UNIDAD', 'Unidades'),
        ('ATENCION', 'Atenciones'),
        ('OPERATIVO', 'Operativos'),
        ('PORCENTAJE', 'Porcentaje'),
        ('HORA', 'Horas'),
    ]

    cargo = models.ForeignKey(Cargo, on_delete=models.CASCADE, related_name='items_medicion')
    periodo = models.ForeignKey(PeriodoMedicion, on_delete=models.CASCADE, related_name='items_medicion')
    nombre_item = models.CharField(max_length=150, verbose_name="Nombre del Ítem")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción de la Prestación")
    tipo_unidad = models.CharField(max_length=20, choices=TIPO_UNIDAD_CHOICES, default='UNIDAD', verbose_name="Unidad de Medida")
    meta_cuantitativa = models.FloatField(default=1.0, help_text="Meta esperada en el período (>0)")
    ponderador = models.FloatField(default=25.0, help_text="Porcentaje de peso ponderado (0 a 100)")
    unidad_medida = models.CharField(max_length=50, default="Actividades")
    medir_compromisos = models.BooleanField(
        default=False,
        verbose_name='Incluir compromisos validados en el avance',
    )
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = 'Items'
        verbose_name = "Ítem"
        verbose_name_plural = "Ítems"
        unique_together = ('cargo', 'periodo', 'nombre_item')

    def clean(self):
        if self.periodo_id and self.periodo.cerrado:
            raise ValidationError(
                'No se pueden crear ni modificar ítems de un período cerrado.'
            )
        if self.meta_cuantitativa <= 0:
            raise ValidationError("La meta del ítem debe ser estrictamente mayor que cero.")
        if not (0 <= self.ponderador <= 100):
            raise ValidationError("El ponderador debe estar entre 0% y 100%.")
        if (
            self.activo
            and self.cargo_id
            and self.periodo_id
            and getattr(self, '_validar_suma_ponderador', True)
        ):
            ponderadores_existentes = ItemMedicion.objects.filter(
                cargo_id=self.cargo_id,
                periodo_id=self.periodo_id,
                activo=True,
            )
            if self.pk:
                ponderadores_existentes = ponderadores_existentes.exclude(pk=self.pk)
            ponderador_total = (
                ponderadores_existentes.aggregate(total=models.Sum('ponderador'))['total']
                or 0
            )
            if ponderador_total + self.ponderador > 100:
                raise ValidationError({
                    'ponderador': 'La suma de los ponderadores activos para el cargo y período no puede superar 100%.',
                })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nombre_item} ({self.cargo.nombre} - {self.ponderador}%)"

    @property
    def nombre(self):
        return self.nombre_item

    @property
    def tipoUnidad(self):
        return self.tipo_unidad

    @property
    def estado(self):
        return self.activo

    @estado.setter
    def estado(self, val):
        self.activo = val


class VersionConfiguracionPeriodoQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Las versiones de configuración son inmutables.')

    def delete(self):
        raise ValidationError('Las versiones de configuración no se pueden eliminar.')


class VersionConfiguracionPeriodo(models.Model):
    periodo = models.ForeignKey(
        PeriodoMedicion,
        on_delete=models.PROTECT,
        related_name='versiones_configuracion',
    )
    version = models.CharField(max_length=50)
    configuracion = models.JSONField()
    creado_por = models.ForeignKey(
        'Usuario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='versiones_configuracion_periodo',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    objects = VersionConfiguracionPeriodoQuerySet.as_manager()

    class Meta:
        ordering = ['periodo_id', 'creado_en']
        constraints = [
            models.UniqueConstraint(
                fields=('periodo', 'version'),
                name='uniq_period_config_version',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Las versiones de configuración son inmutables.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Las versiones de configuración no se pueden eliminar.')

    def obtenerCantidadMetas(self) -> int:
        return self.metas_rel.filter(vigencia=True).count() if hasattr(self, 'metas_rel') else 0

    def activarItem(self) -> bool:
        self.activo = True
        self.save(update_fields=['activo'])
        return True

    def desactivarItem(self) -> bool:
        self.activo = False
        self.save(update_fields=['activo'])
        return True

    def calcular_avance(self, funcionario=None, delegacion=None) -> float:
        """RN-003: Cuenta las unidades validadas según el tipo de medición del ítem."""
        from sgr_app.models import Actividad, Evidencia
        if self.medir_compromisos:
            compromisos = CompromisoAgenda.objects.filter(
                actividad__item_medicion=self,
                actividad__anulada=False,
                actividad__fecha_actividad__range=(
                    self.periodo.fecha_inicio,
                    self.periodo.fecha_termino,
                ),
                fecha_comprometida__range=(
                    self.periodo.fecha_inicio,
                    self.periodo.fecha_termino,
                ),
                estado__in=(
                    CompromisoAgenda.Estado.COMPLETADO,
                    CompromisoAgenda.Estado.REALIZADO,
                    CompromisoAgenda.Estado.CUMPLIDO,
                ),
                actividad__evidencias__estado=Evidencia.Estado.APROBADA,
            ).exclude(
                actividad__evidencias__estado__in=(
                    Evidencia.Estado.PENDIENTE,
                    Evidencia.Estado.RECHAZADA,
                    Evidencia.Estado.CORRECCION,
                )
            )
            if funcionario:
                compromisos = compromisos.filter(responsable=funcionario)
            if delegacion:
                compromisos = compromisos.filter(delegacion=delegacion)
            return float(compromisos.distinct().count())
        qs = Actividad.objects.filter(
            item_medicion=self,
            anulada=False,
            evidencias__estado=Evidencia.Estado.APROBADA,
        )
        if funcionario:
            qs = qs.filter(funcionario=funcionario)
        if delegacion:
            qs = qs.filter(delegacion=delegacion)
        return float(qs.distinct().count())

    calcularAvance = calcular_avance

    def calcular_cumplimiento(self, funcionario=None) -> float:
        """RN-004: % de cumplimiento = (avance / meta) * 100."""
        if self.meta_cuantitativa <= 0:
            return 0.0
        avance = self.calcular_avance(funcionario)
        return round((avance / self.meta_cuantitativa) * 100.0, 2)

    calcularCumplimiento = calcular_cumplimiento

    def calcular_ponderado(self, funcionario=None) -> float:
        """RN-005: Cumplimiento ponderado = (% cumplimiento * ponderador) / 100."""
        c = self.calcular_cumplimiento(funcionario)
        return round((c * float(self.ponderador)) / 100.0, 2)

    calcularPonderado = calcular_ponderado


# =============================================================================
# 1. CLASE ACTIVIDAD
# =============================================================================
class Actividad(models.Model):
    """
    Entidad transaccional central que registra las ejecuciones concretas (Clase Actividad).
    """
    fecha_registro = models.DateTimeField(auto_now_add=True, help_text="Fecha y hora generada por el servidor")
    fecha_actividad = models.DateField(default=timezone.localdate, verbose_name="Fecha de Ejecución")
    codigo_registro = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        blank=True,
        verbose_name="Código de Actividad",
    )
    anulada = models.BooleanField(default=False, verbose_name="Actividad Anulada")
    anulada_en = models.DateTimeField(null=True, blank=True)
    anulada_por = models.ForeignKey(
        'Usuario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actividades_anuladas',
    )
    motivo_anulacion = models.TextField(blank=True, default='')
    funcionario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='actividades', verbose_name="Funcionario Ejecutor")
    delegacion = models.ForeignKey(Delegacion, on_delete=models.CASCADE, related_name='actividades')
    item = models.ForeignKey(ItemMedicion, on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades', verbose_name="Ítem Catálogo")
    item_medicion = models.ForeignKey(ItemMedicion, on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades_legacy')
    catalogo_servicio = models.ForeignKey(CatalogoServicio, on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades')
    catalogo_servicio_nombre = models.CharField(max_length=150, blank=True, default='')
    catalogo_servicio_area = models.CharField(max_length=100, blank=True, default='')
    tipo_actividad = models.ForeignKey(
        TipoActividad,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actividades',
    )
    tipo_actividad_nombre = models.CharField(max_length=100, blank=True, default='')
    tipo_actividad_area = models.CharField(max_length=100, blank=True, default='')
    solicitante = models.ForeignKey(Solicitante, on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades', verbose_name="Solicitante / Beneficiario")

    solicitud_problema = models.TextField(blank=True, default='', verbose_name="Solicitud o Problema Detectado")
    actividad_solicitud = models.CharField(max_length=255, blank=True, default='', help_text="Problema atendido o solicitud")
    accion = models.TextField(blank=True, default='', verbose_name="Acción / Gestión Realizada")
    accion_ejecutada = models.TextField(blank=True, default='', help_text="Acción desarrollada")
    direccion = models.CharField(max_length=255, blank=True, null=True, help_text="Dirección o sector de la intervención")
    contacto_nombre = models.CharField(max_length=150, blank=True, null=True, help_text="Nombre de referencia anonimizado")
    contacto_telefono = models.CharField(max_length=50, blank=True, null=True, validators=[validate_chilean_phone], help_text="Teléfono anonimizado")

    latitud = models.FloatField(blank=True, null=True)
    longitud = models.FloatField(blank=True, null=True)

    compromiso_agenda = models.ForeignKey('CompromisoAgenda', on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades_derivadas')

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'Actividades'
        verbose_name = "Actividad"
        verbose_name_plural = "Actividades"
        ordering = ['-fecha_registro']

    def __str__(self):
        return f"Actividad #{self.id} - {self.funcionario.username} ({self.fecha_actividad})"

    def save(self, *args, **kwargs):
        if self._state.adding and not self.codigo_registro:
            prefijo = (
                self.delegacion.codigo
                if self.delegacion_id
                else "SGR"
            )
            self.codigo_registro = f'{prefijo}{secrets.token_hex(8).upper()}'
        if self.pk and not self._state.adding:
            codigo_guardado = type(self).objects.filter(
                pk=self.pk,
            ).values_list('codigo_registro', flat=True).first()
            if codigo_guardado != self.codigo_registro:
                raise ValidationError(
                    {'codigo_registro': 'El código de actividad es inmutable.'}
                )
        if self.contacto_telefono:
            self.contacto_telefono = normalize_chilean_phone(self.contacto_telefono)
        if self._state.adding and self.catalogo_servicio_id:
            if not self.catalogo_servicio_nombre or not self.catalogo_servicio_area:
                servicio = self.catalogo_servicio
                if not self.catalogo_servicio_nombre:
                    self.catalogo_servicio_nombre = servicio.nombre
                if not self.catalogo_servicio_area:
                    self.catalogo_servicio_area = servicio.area or ''
        if self._state.adding and self.tipo_actividad_id:
            if (
                self.funcionario.area_entidad_id
                and self.tipo_actividad.area_id
                != self.funcionario.area_entidad_id
            ):
                raise ValidationError({
                    'tipo_actividad': (
                        'El tipo de actividad debe pertenecer al área del funcionario.'
                    )
                })
            self.tipo_actividad_nombre = self.tipo_actividad.nombre
            self.tipo_actividad_area = self.tipo_actividad.area.nombre
        database_alias = kwargs.get('using')
        if database_alias is None and len(args) > 2:
            database_alias = args[2]
        connection = connections[
            database_alias or router.db_for_write(type(self), instance=self)
        ]
        for attempt in range(6):
            try:
                if connection.in_atomic_block:
                    with transaction.atomic(using=connection.alias):
                        super().save(*args, **kwargs)
                else:
                    super().save(*args, **kwargs)
                break
            except OperationalError as exc:
                message = str(exc).lower()
                sqlite_lock = connection.vendor == 'sqlite' and (
                    'database table is locked' in message
                    or 'database is locked' in message
                )
                if not sqlite_lock or attempt == 5:
                    raise
                time.sleep(0.02 * (2 ** attempt))

    def anular(self, usuario, motivo):
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValidationError({'motivo_anulacion': 'Indica el motivo de la anulación.'})
        if self.anulada:
            raise ValidationError({'anulada': 'La actividad ya está anulada.'})
        self.anulada = True
        self.anulada_en = timezone.now()
        self.anulada_por = usuario
        self.motivo_anulacion = motivo
        self.save(update_fields=[
            'anulada',
            'anulada_en',
            'anulada_por',
            'motivo_anulacion',
            'actualizado_en',
        ])

    @property
    def fechaActividad(self):
        return self.fecha_actividad

    @property
    def solicitudProblema(self):
        return self.solicitud_problema or self.actividad_solicitud

    def asociar_solicitante(self, solicitante_obj):
        """Asignar formalmente el vecino, institución o beneficiario."""
        if isinstance(solicitante_obj, Solicitante):
            self.solicitante = solicitante_obj
            self.contacto_nombre = str(solicitante_obj)
        else:
            self.contacto_nombre = str(solicitante_obj)
        self.save()

    asociarSolicitante = asociar_solicitante

    def generar_compromiso(self, datos_compromiso=None, **kwargs):
        """Crear un compromiso de seguimiento derivado de esta actividad."""
        datos = {}
        if isinstance(datos_compromiso, dict):
            datos.update(datos_compromiso)
        datos.update(kwargs)

        origen = datos.get('origen', self.contacto_nombre or 'Solicitante')
        territorio = datos.get('territorio', self.delegacion.nombre)
        fecha_comp = datos.get('fecha_compromiso', datos.get('fecha_comprometida', timezone.localdate()))
        area_apoyo = datos.get('area_apoyo', 'Operaciones')
        obs = datos.get('observacion', datos.get('observaciones', ''))

        deleg = self.delegacion
        if hasattr(territorio, 'pk'):
            deleg = territorio

        comp = CompromisoAgenda.objects.create(
            actividad=self,
            solicitante=str(origen),
            origen=str(origen),
            territorio=str(territorio) if not hasattr(territorio, 'nombre') else territorio.nombre,
            delegacion=deleg,
            responsable=self.funcionario,
            fecha_compromiso=fecha_comp,
            fecha_comprometida=fecha_comp,
            area_apoyo=area_apoyo,
            estado='PENDIENTE',
            observacion=obs,
            observaciones=obs,
            creado_por=self.funcionario
        )
        self.compromiso_agenda = comp
        self.save(update_fields=['compromiso_agenda'])
        return comp

    def generarCompromiso(self, origen, territorio, fecha_compromiso, area_apoyo, observacion=""):
        return self.generar_compromiso(
            origen=origen, territorio=territorio, fecha_compromiso=fecha_compromiso,
            area_apoyo=area_apoyo, observacion=observacion
        )

    def adjuntar_evidencia(self, archivo_file, descripcion=""):
        """Cargar y vincular un archivo de respaldo a la clase Evidencia."""
        es_archivo = hasattr(archivo_file, 'read')
        metadatos = (
            metadatos_archivo_evidencia(archivo_file, self.funcionario)
            if es_archivo else None
        )
        evidencia, created = Evidencia.objects.get_or_create(
            actividad=self,
            defaults={
                'archivo': archivo_file if es_archivo else None,
                'archivo_foto': archivo_file if es_archivo else None,
                'url_foto_simulada': archivo_file if isinstance(archivo_file, str) else '',
                'observacion': descripcion,
                'estado': Evidencia.Estado.PENDIENTE,
                'subido_por': self.funcionario if es_archivo else None,
                'metadatos': metadatos,
            }
        )
        if not created:
            if es_archivo:
                evidencia.archivo = archivo_file
                evidencia.archivo_foto = archivo_file
                evidencia.subido_por = self.funcionario
                evidencia.metadatos = metadatos
            elif isinstance(archivo_file, str):
                evidencia.url_foto_simulada = archivo_file
            evidencia.observacion = descripcion
            evidencia.save()
        return evidencia

    @property
    def evidencia(self):
        # RNF-002: usa las evidencias precargadas (prefetch_related) si existen,
        # para no lanzar una consulta por cada actividad de un listado.
        precargadas = getattr(self, '_prefetched_objects_cache', {}).get('evidencias')
        if precargadas is not None:
            return min(precargadas, key=lambda evidencia: evidencia.pk, default=None)
        return self.evidencias.order_by('pk').first()

    adjuntarEvidencia = adjuntar_evidencia


# =============================================================================
# 2. CLASE COMPROMISO / COMPROMISOAGENDA
# =============================================================================
class CompromisoVigenteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(anulado=False)


class CompromisoAgenda(models.Model):
    """
    Entidad de seguimiento operativo asociada a una actividad (Clase Compromiso).
    """
    ESTADO_CHOICES = [
        ('INGRESADO', 'Ingresado'),
        ('PENDIENTE', 'Pendiente'),
        ('EN_PROCESO', 'En Proceso'),
        ('COMPLETADO', 'Completado'),
        ('VENCIDO', 'Vencido'),
        ('REPROGRAMADO', 'Reprogramado'),
        ('CUMPLIDO', 'Cumplido'),
        ('REALIZADO', 'Realizado'),
    ]

    class Estado(models.TextChoices):
        INGRESADO = 'INGRESADO', 'Ingresado'
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        EN_PROCESO = 'EN_PROCESO', 'En Proceso'
        COMPLETADO = 'COMPLETADO', 'Completado'
        VENCIDO = 'VENCIDO', 'Vencido'
        REPROGRAMADO = 'REPROGRAMADO', 'Reprogramado'
        REALIZADO = 'REALIZADO', 'Realizado'
        CUMPLIDO = 'CUMPLIDO', 'Cumplido'

    origen = models.CharField(max_length=100, default='Vecino', verbose_name="Origen del Compromiso")
    solicitante = models.CharField(max_length=150, help_text="Nombre anonimizado de vecino u organización")
    territorio = models.CharField(max_length=150, default='La Serena', verbose_name="Territorio / Sector")
    delegacion = models.ForeignKey(Delegacion, on_delete=models.CASCADE, related_name='compromisos')
    responsable = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='compromisos_asignados')
    fecha_registro = models.DateTimeField(auto_now_add=True)
    fecha_compromiso = models.DateField(default=timezone.localdate, verbose_name="Fecha Comprometida")
    fecha_comprometida = models.DateField(default=timezone.localdate)
    area_apoyo = models.CharField(max_length=150, default='Operaciones', verbose_name="Área / Unidad de Apoyo")
    descripcion = models.TextField(blank=True, default='')
    estado = models.CharField(max_length=30, choices=ESTADO_CHOICES, default='PENDIENTE', verbose_name="Estado")
    observacion = models.TextField(blank=True, null=True, verbose_name="Observaciones / Avances")
    observaciones = models.TextField(blank=True, null=True)
    actividad = models.ForeignKey(Actividad, on_delete=models.CASCADE, null=True, blank=True, related_name='compromisos', verbose_name="Actividad Origen")
    creado_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, related_name='compromisos_creados')
    actualizado_en = models.DateTimeField(auto_now=True)
    fecha_realizacion = models.DateTimeField(null=True, blank=True)
    # Baja lógica: por normativa los compromisos no se eliminan, se anulan con motivo.
    anulado = models.BooleanField(default=False, verbose_name="Compromiso Anulado")
    anulado_en = models.DateTimeField(null=True, blank=True)
    anulado_por = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='compromisos_anulados',
    )
    motivo_anulacion = models.TextField(blank=True, default='')

    # `objects` solo entrega compromisos vigentes (tableros, alertas, indicadores);
    # `todos` incluye los anulados para historial y auditoría.
    objects = CompromisoVigenteManager()
    todos = models.Manager()

    class Meta:
        db_table = 'Compromisos'
        verbose_name = "Compromiso"
        verbose_name_plural = "Compromisos"
        ordering = ['fecha_compromiso']
        base_manager_name = 'todos'

    def anular(self, usuario, motivo):
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValidationError({'motivo_anulacion': 'Indica el motivo de la anulación.'})
        if self.anulado:
            raise ValidationError({'anulado': 'El compromiso ya está anulado.'})
        self.anulado = True
        self.anulado_en = timezone.now()
        self.anulado_por = usuario
        self.motivo_anulacion = motivo
        self.save(update_fields=[
            'anulado',
            'anulado_en',
            'anulado_por',
            'motivo_anulacion',
            'actualizado_en',
        ])

    def reactivar(self):
        """Revierte una anulación. Los datos de la anulación quedan en la auditoría."""
        if not self.anulado:
            raise ValidationError({'anulado': 'El compromiso no está anulado.'})
        self.anulado = False
        self.anulado_en = None
        self.anulado_por = None
        self.motivo_anulacion = ''
        self.save(update_fields=[
            'anulado',
            'anulado_en',
            'anulado_por',
            'motivo_anulacion',
            'actualizado_en',
        ])

    def __str__(self):
        return f"Compromiso #{self.id} - {self.territorio} ({self.estado})"

    @property
    def estado_tablero(self):
        evidencias = self.actividad.evidencias.all() if self.actividad_id else Evidencia.objects.none()
        if evidencias.exists() and not evidencias.exclude(estado=Evidencia.Estado.APROBADA).exists():
            return self.Estado.COMPLETADO
        fecha_limite = self.fecha_comprometida or self.fecha_compromiso
        if fecha_limite < timezone.localdate() and self.estado not in [
            self.Estado.COMPLETADO, self.Estado.REALIZADO, 'CUMPLIDO'
        ]:
            return self.Estado.VENCIDO
        if self.estado == self.Estado.REPROGRAMADO:
            return self.Estado.REPROGRAMADO
        if evidencias.exists():
            return self.Estado.EN_PROCESO
        return self.estado

    @property
    def realizado_fuera_de_plazo(self):
        fecha_limite = self.fecha_comprometida or self.fecha_compromiso
        return bool(
            self.fecha_realizacion
            and fecha_limite
            and self.fecha_realizacion.date() > fecha_limite
        )

    def puede_transicionar_a(self, nuevo_estado):
        transiciones = {
            self.Estado.INGRESADO: {
                self.Estado.PENDIENTE,
                self.Estado.EN_PROCESO,
                self.Estado.REPROGRAMADO,
            },
            self.Estado.PENDIENTE: {
                self.Estado.EN_PROCESO,
                self.Estado.REPROGRAMADO,
                self.Estado.REALIZADO,
            },
            self.Estado.EN_PROCESO: {
                self.Estado.PENDIENTE,
                self.Estado.REPROGRAMADO,
                self.Estado.REALIZADO,
            },
            self.Estado.REPROGRAMADO: {
                self.Estado.PENDIENTE,
                self.Estado.EN_PROCESO,
                self.Estado.REALIZADO,
            },
            self.Estado.COMPLETADO: {
                self.Estado.EN_PROCESO,
                self.Estado.REALIZADO,
            },
            self.Estado.REALIZADO: {self.Estado.EN_PROCESO},
        }
        return nuevo_estado == self.estado or nuevo_estado in transiciones.get(
            self.estado,
            set(),
        )

    def registrar_transicion(self, nuevo_estado, autor, observacion):
        observacion = (observacion or '').strip()
        if not observacion:
            raise ValidationError({
                'observacion': 'Explica el cambio de estado en la observación.'
            })
        if not self.puede_transicionar_a(nuevo_estado):
            raise ValidationError({
                'estado': f'No se permite cambiar de {self.estado} a {nuevo_estado}.'
            })
        if nuevo_estado == self.Estado.REALIZADO:
            if not self.actividad_id:
                raise ValidationError({'estado': 'El compromiso no tiene una actividad asociada. Edítalo para asociarle una actividad y adjunta la evidencia; podrá marcarse como realizado cuando el Delegado la apruebe.'})
            evidencias = (
                self.actividad.evidencias.all()
                if self.actividad_id
                else Evidencia.objects.none()
            )
            if not evidencias.exists() or evidencias.exclude(
                estado=Evidencia.Estado.APROBADA,
            ).exists():
                raise ValidationError({
                    'estado': (
                        'Para marcar el compromiso como realizado, todas sus evidencias '
                        'deben estar aprobadas.'
                    )
                })

        estado_anterior = self.estado
        ahora = timezone.now()
        self.estado = nuevo_estado
        self.fecha_realizacion = (
            self.fecha_realizacion or ahora
            if nuevo_estado == self.Estado.REALIZADO
            else None
        )
        self.observacion = observacion
        self.observaciones = observacion
        self.save(update_fields=[
            'estado',
            'fecha_realizacion',
            'observacion',
            'observaciones',
            'actualizado_en',
        ])
        ObservacionCompromiso.objects.create(
            compromiso=self,
            autor=autor,
            texto=observacion,
        )
        return estado_anterior

    def sincronizar_estado_evidencias(self):
        evidencias = self.actividad.evidencias.all() if self.actividad_id else Evidencia.objects.none()
        completado = evidencias.exists() and not evidencias.exclude(
            estado=Evidencia.Estado.APROBADA
        ).exists()
        self.estado = self.Estado.COMPLETADO if completado else self.Estado.EN_PROCESO
        if completado and not self.fecha_realizacion:
            self.fecha_realizacion = (
                evidencias.order_by('-fecha_validacion')
                .values_list('fecha_validacion', flat=True)
                .first()
                or timezone.now()
            )
        elif not completado:
            self.fecha_realizacion = None
        self.save(update_fields=[
            'estado',
            'fecha_realizacion',
            'actualizado_en',
        ])

    @property
    def fechaCompromiso(self):
        return self.fecha_compromiso

    @fechaCompromiso.setter
    def fechaCompromiso(self, val):
        self.fecha_compromiso = val
        self.fecha_comprometida = val

    @property
    def areaApoyo(self):
        return self.area_apoyo

    @areaApoyo.setter
    def areaApoyo(self, val):
        self.area_apoyo = val

    @property
    def montoEjecutado(self):
        return 1.0 if self.estado in ['REALIZADO', 'COMPLETADO', 'CUMPLIDO'] else 0.0

    def actualizar_estado(self, nuevo_estado: str, observacion_nueva=""):
        """Modificar la fase del compromiso y registrar observación."""
        self.estado = nuevo_estado
        if observacion_nueva:
            self.observacion = f"{self.observacion or ''}\n[{date.today()}]: {observacion_nueva}"
            self.observaciones = self.observacion
        self.save()

    actualizarEstado = actualizar_estado

    def fecha_vencida(self) -> bool:
        """Evalúa si la fecha límite ya sobrepasó hoy sin haber sido cumplido."""
        if self.estado in ['CUMPLIDO', 'REALIZADO', 'COMPLETADO', 'CANCELADO']:
            return False
        return self.fecha_compromiso < date.today()

    fechaVencida = fecha_vencida

    def calcularDiasAtraso(self) -> int:
        if self.fecha_vencida():
            diferencia = date.today() - self.fecha_compromiso
            return diferencia.days
        return 0

    def reprogramarFecha(self, nueva_fecha: date, motivo: str):
        fecha_anterior = self.fecha_compromiso
        self.fecha_compromiso = nueva_fecha
        self.fecha_comprometida = nueva_fecha
        self.estado = self.Estado.REPROGRAMADO
        nota = f"\n[Reprogramado el {date.today()}]: Fecha anterior ({fecha_anterior}) -> Nueva fecha ({nueva_fecha}). Motivo: {motivo}"
        self.observacion = (self.observacion or "") + nota
        self.observaciones = self.observacion
        self.save()

    def marcarCompletado(self):
        self.estado = self.Estado.COMPLETADO
        self.save()


class ObservacionCompromiso(models.Model):
    compromiso = models.ForeignKey(
        CompromisoAgenda,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='historial_observaciones',
    )
    autor = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        related_name='observaciones_compromisos',
    )
    texto = models.TextField()
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creada_en', 'pk']
        verbose_name = 'Observación de compromiso'
        verbose_name_plural = 'Observaciones de compromisos'

    def __str__(self):
        return f"Observación del compromiso #{self.compromiso_id or 'eliminado'}"


class ComentarioActividad(models.Model):
    actividad = models.ForeignKey(
        Actividad,
        on_delete=models.CASCADE,
        related_name='comentarios',
    )
    autor = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        related_name='comentarios_actividades',
    )
    texto = models.TextField()
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado_en', 'pk']

    def clean(self):
        self.texto = (self.texto or '').strip()
        if not self.texto:
            raise ValidationError({'texto': 'El comentario no puede estar vacío.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# =============================================================================
# 3. CLASE EVIDENCIA
# =============================================================================
class EvidenciaVigenteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(reemplazada_en__isnull=True)


class Evidencia(models.Model):
    """
    Entidad que almacena los medios de prueba y archivos adjuntos (Clase Evidencia).
    """
    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente de Validación'
        APROBADA = 'APROBADA', 'Aprobada'
        RECHAZADA = 'RECHAZADA', 'Rechazada'
        CORRECCION = 'CORRECCION', 'Requiere Corrección'

    actividad = models.ForeignKey(Actividad, on_delete=models.CASCADE, related_name='evidencias', verbose_name="Actividad Asociada")
    codigo_verificador = models.CharField(max_length=30, unique=True, editable=False, db_index=True)
    archivo = models.FileField(upload_to='evidencias/%Y/%m/', blank=True, null=True, verbose_name="Archivo Adjunto")
    archivo_foto = models.ImageField(upload_to='evidencias/%Y/%m/', blank=True, null=True)
    archivo_foto_2 = models.ImageField(upload_to='evidencias/%Y/%m/', blank=True, null=True)
    archivo_foto_3 = models.ImageField(upload_to='evidencias/%Y/%m/', blank=True, null=True)
    url_foto_simulada = models.URLField(max_length=500, blank=True, null=True)
    url_foto_simulada_2 = models.URLField(max_length=500, blank=True, null=True)
    url_foto_simulada_3 = models.URLField(max_length=500, blank=True, null=True)
    fecha_evidencia = models.DateField(default=timezone.now, blank=True, verbose_name="Fecha de Carga")
    metadatos = models.JSONField(blank=True, null=True, verbose_name="Metadatos (GPS, formato, peso)")
    subido_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='evidencias_subidas',
    )
    # Retención (RNF-017): al vencer, los archivos se deshabilitan; nunca se borran.
    archivos_deshabilitados_en = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE, verbose_name="Estado de Validación")
    observacion = models.TextField(blank=True, null=True, verbose_name="Observacion")
    observacion_rechazo = models.TextField(blank=True, null=True)
    hash_sha256 = models.CharField(max_length=64, blank=True, null=True)
    verificador = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name='evidencias_validadas')
    fecha_validacion = models.DateTimeField(null=True, blank=True)
    fecha_subida = models.DateTimeField(auto_now_add=True)
    # Reemplazo: por normativa la foto anterior no se borra; queda en el historial
    # (`todas`) y deja de contar en la revisión y el avance (`objects`).
    reemplazada_en = models.DateTimeField(null=True, blank=True)
    reemplazada_por = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='evidencias_reemplazadas',
    )

    objects = EvidenciaVigenteManager()
    todas = models.Manager()

    class Meta:
        base_manager_name = 'todas'
        db_table = 'Evidencias'
        verbose_name = "Evidencia"
        verbose_name_plural = "Evidencias"

    @property
    def get_foto_url(self):
        if self.archivos_deshabilitados_en:
            return '/static/img/evidencia_terreno_placeholder.svg'
        if self.archivo:
            try:
                return self.archivo.url
            except Exception:
                pass
        if self.archivo_foto:
            try:
                return self.archivo_foto.url
            except Exception:
                pass
        if self.url_foto_simulada:
            return self.url_foto_simulada
        return '/static/img/evidencia_terreno_placeholder.svg'

    @property
    def get_foto_url_2(self):
        if self.archivos_deshabilitados_en:
            return ''
        if self.archivo_foto_2:
            try:
                return self.archivo_foto_2.url
            except Exception:
                pass
        return self.url_foto_simulada_2 or ''

    @property
    def get_foto_url_3(self):
        if self.archivos_deshabilitados_en:
            return ''
        if self.archivo_foto_3:
            try:
                return self.archivo_foto_3.url
            except Exception:
                pass
        return self.url_foto_simulada_3 or ''

    CAMPOS_ARCHIVO = ('archivo_foto', 'archivo_foto_2', 'archivo_foto_3')

    def reemplazar(self, usuario):
        """Retira la evidencia de la revisión sin borrarla (queda en el historial)."""
        self.reemplazada_en = timezone.now()
        self.reemplazada_por = usuario
        self.save(update_fields=['reemplazada_en', 'reemplazada_por'])

    MENSAJE_AUTOVALIDACION = (
        'No puedes validar evidencias de tus propias actividades ni las que subiste tú: '
        'debe revisarlas otra persona (segregación de funciones, RN-009).'
    )

    def es_autovalidacion(self, usuario):
        """RNF-005: nadie valida evidencias de su propia actividad o que subió él mismo."""
        return bool(usuario and usuario.pk) and (
            self.actividad.funcionario_id == usuario.pk or self.subido_por_id == usuario.pk
        )

    def _metadatos_archivos(self):
        """Lista de metadatos por archivo (admite ambos formatos guardados)."""
        metadatos = self.metadatos or {}
        if isinstance(metadatos, dict) and isinstance(metadatos.get('archivos'), list):
            return [m for m in metadatos['archivos'] if isinstance(m, dict)]
        if isinstance(metadatos, dict) and metadatos.get('sha256'):
            return [metadatos]
        return []

    def _hash_registrado(self):
        for metadato in self._metadatos_archivos():
            if metadato.get('sha256'):
                return metadato['sha256']
        return None

    def calcular_sha256(self) -> str:
        """RN-010: SHA-256 del contenido real de la fotografía principal."""
        from sgr_app.services.evidencias import sha256_archivo

        for campo in self.CAMPOS_ARCHIVO:
            archivo = getattr(self, campo)
            if archivo:
                with archivo.open('rb') as contenido:
                    return sha256_archivo(contenido)
        return None

    def verificar_integridad(self):
        """
        Compara el SHA-256 actual de cada archivo con el registrado al subirlo.

        Devuelve True si coinciden, False si alguno fue alterado y None si la
        evidencia no tiene hashes registrados (cargas anteriores a este control).
        """
        from sgr_app.services.evidencias import sha256_archivo

        esperados = [m.get('sha256') for m in self._metadatos_archivos() if m.get('sha256')]
        if not esperados:
            return None
        actuales = []
        for campo in self.CAMPOS_ARCHIVO:
            archivo = getattr(self, campo)
            if archivo:
                with archivo.open('rb') as contenido:
                    actuales.append(sha256_archivo(contenido))
        return sorted(actuales) == sorted(esperados)

    calcularSha256 = calcular_sha256

    def generar_codigo_verificador(self) -> str:
        """RN-010: Genera código único institucional EVI-AAAA-MM-XXXX."""
        import uuid
        code = f"EVI-2026-{uuid.uuid4().hex[:6].upper()}"
        self.codigo_verificador = code
        return code

    generarCodigoVerificador = generar_codigo_verificador

    @property
    def fotos_adicionales(self):
        fotos = []
        if self.get_foto_url_2:
            fotos.append({'num': 2, 'url': self.get_foto_url_2})
        if self.get_foto_url_3:
            fotos.append({'num': 3, 'url': self.get_foto_url_3})
        return fotos

    @property
    def fechaEvidencia(self):
        return self.fecha_evidencia

    @property
    def estadoValidacion(self):
        return "VALIDADA" if self.estado else ("RECHAZADA" if self.observacion else "EN_REVISION")

    def cargar_archivo(self, archivo_subido, metadatos_dict=None, subido_por=None):
        if hasattr(archivo_subido, 'read'):
            metadatos_dict = metadatos_archivo_evidencia(
                archivo_subido,
                subido_por or self.actividad.funcionario,
            )
            self.subido_por = subido_por or self.actividad.funcionario
        self.archivo = archivo_subido
        if hasattr(archivo_subido, 'read'):
            self.archivo_foto = archivo_subido
        elif isinstance(archivo_subido, str):
            self.url_foto_simulada = archivo_subido
        if metadatos_dict:
            self.metadatos = metadatos_dict
        self.fecha_evidencia = date.today()
        self.save()

    cargarArchivo = cargar_archivo

    def solicitar_validacion(self):
        self.estado = self.Estado.PENDIENTE
        self.observacion_rechazo = None
        self.save()

    solicitarValidacion = solicitar_validacion

    def validar_evidencia(self, aprobada: bool = True, motivo_rechazo=""):
        self.estado = self.Estado.APROBADA if aprobada else self.Estado.RECHAZADA
        if not aprobada:
            self.observacion_rechazo = motivo_rechazo
            self.observacion = motivo_rechazo
        else:
            self.observacion_rechazo = None
        self.fecha_validacion = timezone.now()
        self.save()

    def validarEvidencia(self, observacion: str = ""):
        self.validar_evidencia(aprobada=True, motivo_rechazo=observacion)

    def rechazarEvidencia(self, observacion: str):
        self.validar_evidencia(aprobada=False, motivo_rechazo=observacion)

    def save(self, *args, **kwargs):
        if isinstance(self.estado, bool):
            self.estado = self.Estado.APROBADA if self.estado else (self.Estado.RECHAZADA if self.observacion else self.Estado.PENDIENTE)

        # El hash proviene del contenido saneado del archivo al subirlo.
        hash_registrado = self._hash_registrado()
        if hash_registrado:
            self.hash_sha256 = hash_registrado
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'hash_sha256'}

        if not self.codigo_verificador:
            prefijo = self.actividad.delegacion.codigo if self.actividad and self.actividad.delegacion else "SGR"
            codigos_intentados = set()
            for _ in range(5):
                codigo = generar_codigo_evidencia(prefijo)
                if codigo in codigos_intentados:
                    continue
                codigos_intentados.add(codigo)
                self.codigo_verificador = codigo
                try:
                    with transaction.atomic():
                        super().save(*args, **kwargs)
                    break
                except IntegrityError:
                    if not type(self)._base_manager.filter(
                        codigo_verificador=codigo,
                    ).exists():
                        raise
            else:
                raise ValidationError(
                    'No fue posible asignar un código único a la evidencia después de varios intentos.'
                )
        else:
            if self.pk and not self._state.adding:
                codigo_guardado = type(self)._base_manager.filter(
                    pk=self.pk,
                ).values_list('codigo_verificador', flat=True).first()
                if codigo_guardado != self.codigo_verificador:
                    raise ValidationError(
                        {'codigo_verificador': 'El código verificador es inmutable.'}
                    )
            super().save(*args, **kwargs)
        compromiso = self.actividad.compromiso_agenda if self.actividad_id else None
        if compromiso:
            compromiso.sincronizar_estado_evidencias()

    def __str__(self):
        estado_str = "Aprobada" if self.estado else "Pendiente/Rechazada"
        return f"Evidencia #{self.id} - Actividad #{self.actividad.id} ({estado_str})"


# =============================================================================
# 12. CLASE META / METAMODEL
# =============================================================================
class MetaModel(models.Model):
    """
    Entidad de Meta en el modelo de dominio UML (Clase Meta).
    """
    TIPO_CHOICES = [
        ('CUANTITATIVO', 'Cuantitativo'),
        ('PORCENTUAL', 'Porcentual'),
    ]

    nombre = models.CharField(max_length=150, verbose_name='Nombre de la Meta')
    tipo_meta = models.CharField(max_length=20, choices=TIPO_CHOICES, default='CUANTITATIVO')
    valor_meta = models.FloatField(default=1.0, verbose_name='Valor Objetivo / Meta')
    unidad = models.CharField(max_length=50, default='Atenciones', verbose_name='Unidad de Medida')
    ponderador = models.FloatField(default=25.0, verbose_name='Ponderación (%)')
    vigencia = models.BooleanField(default=True, verbose_name='Vigencia')
    periodo = models.ForeignKey(PeriodoMedicion, on_delete=models.CASCADE, related_name='metas', verbose_name='Período')
    item = models.ForeignKey(ItemMedicion, on_delete=models.CASCADE, related_name='metas_rel', null=True, blank=True)
    funcionario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='metas', null=True, blank=True)
    delegacion = models.ForeignKey(Delegacion, on_delete=models.CASCADE, related_name='metas', null=True, blank=True)
    trimestre = models.PositiveSmallIntegerField(default=3)
    anio = models.PositiveIntegerField(default=2026)

    class Meta:
        db_table = 'Metas'
        verbose_name = 'Meta'
        verbose_name_plural = 'Metas'

    def __str__(self):
        return f"{self.nombre} ({self.periodo})"

    @property
    def valorMeta(self):
        return self.valor_meta

    @valorMeta.setter
    def valorMeta(self, val):
        self.valor_meta = val

    @property
    def tipoMeta(self):
        return self.tipo_meta

    @property
    def meta_esperada_al_dia(self) -> float:
        """RF-026 / RN-007: Calcula el objetivo esperado según días transcurridos."""
        porcentaje_esperado = self.periodo.calcular_meta_esperada_al_dia()
        return round((self.valor_meta * porcentaje_esperado) / 100.0, 2)

    metaEsperadaAlDia = meta_esperada_al_dia

    @property
    def cumplimiento_ponderado(self) -> float:
        """RN-005: Multiplica el % de cumplimiento por el ponderador de la meta."""
        porcentaje_cumplimiento = self.calcular_meta()
        factor = self.ponderador / 100.0 if self.ponderador > 1.0 else self.ponderador
        return round(porcentaje_cumplimiento * factor, 2)

    cumplimientoPonderado = cumplimiento_ponderado

    def calcular_meta(self) -> float:
        """Calcula el acumulado de avances a través de los indicadores asociados o actividades."""
        if self.valor_meta <= 0:
            return 0.0
        if hasattr(self, 'indicadores') and self.indicadores.exists():
            total_avanzado = sum(ind.avance for ind in self.indicadores.all())
        elif self.item_id and self.item.medir_compromisos:
            total_avanzado = self.item.calcular_avance(
                self.funcionario,
                self.delegacion,
            )
        else:
            actividades = Actividad.objects.filter(
                anulada=False,
                fecha_actividad__gte=self.periodo.fecha_inicio,
                fecha_actividad__lte=self.periodo.fecha_termino,
                evidencias__estado=Evidencia.Estado.APROBADA,
            )
            if self.item_id:
                actividades = actividades.filter(item_medicion_id=self.item_id)
            if self.funcionario_id:
                actividades = actividades.filter(funcionario_id=self.funcionario_id)
            if self.delegacion_id:
                actividades = actividades.filter(delegacion_id=self.delegacion_id)
            total_avanzado = actividades.distinct().count()
        return round((total_avanzado / self.valor_meta) * 100.0, 2)

    calcularMeta = calcular_meta

    def validar_ponderacion(self) -> bool:
        """RN-001: Valida que las metas del período no excedan el 100%."""
        metas_periodo = MetaModel.objects.filter(periodo=self.periodo, vigencia=True)
        if self.pk:
            metas_periodo = metas_periodo.exclude(pk=self.pk)
        suma_ponderadores = sum(m.ponderador for m in metas_periodo) + self.ponderador
        return suma_ponderadores <= 100.0

    validarPonderacion = validar_ponderacion

    def obtenerEstadoSemaforo(self) -> str:
        avance = self.calcular_meta()
        esperado = self.periodo.calcular_meta_esperada_al_dia()
        if esperado <= 0 or avance >= esperado * self.periodo.umbral_semaforo_verde / 100:
            return 'VERDE'
        if avance >= esperado * self.periodo.umbral_semaforo_ambar / 100:
            return 'AMARILLO'
        return 'ROJO'

    def calcularDesvio(self) -> float:
        if hasattr(self, 'indicadores') and self.indicadores.exists():
            total_avanzado = sum(ind.avance for ind in self.indicadores.all())
        else:
            total_avanzado = 0.0
        return round(total_avanzado - self.meta_esperada_al_dia, 2)

    def clean(self):
        if self.periodo_id and self.periodo.cerrado:
            raise ValidationError(
                'No se pueden crear ni modificar metas de un período cerrado.'
            )
        if self.valor_meta is not None and self.valor_meta <= 0:
            raise ValidationError("RN-002: El valor objetivo de la meta debe ser mayor que cero.")
        if self.ponderador is not None and self.ponderador <= 0:
            raise ValidationError("RN-001: El ponderador debe ser mayor que cero.")
        if (
            self.vigencia
            and self.periodo_id
            and self.ponderador is not None
            and not self.validar_ponderacion()
        ):
            raise ValidationError({
                'ponderador': 'La suma de las ponderaciones vigentes del período no puede superar 100%.',
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


Meta = MetaModel


# =============================================================================
# 14. CLASE INDICADOR
# =============================================================================
class Indicador(models.Model):
    """
    Entidad Indicador del modelo de dominio UML (Clase Indicador).
    """
    avance = models.FloatField(default=0.0, verbose_name='Avance Registrado')
    cumplimiento = models.FloatField(default=0.0, verbose_name='Porcentaje de Cumplimiento (%)')
    ponderacion = models.FloatField(default=0.0, verbose_name='Ponderación (%)')
    semaforo = models.CharField(max_length=20, default='ROJO', verbose_name='Estado Semáforo')
    fecha_calculo = models.DateField(default=timezone.now, verbose_name='Fecha de Cálculo')
    meta = models.ForeignKey(MetaModel, on_delete=models.CASCADE, null=True, blank=True, related_name='indicadores', verbose_name='Meta')
    periodo = models.ForeignKey(PeriodoMedicion, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        db_table = 'Indicadores'
        verbose_name = 'Indicador'
        verbose_name_plural = 'Indicadores'

    def __str__(self):
        return f"Indicador {self.id} - Ponderación: {self.ponderacion}% - Semáforo: {self.semaforo}"

    @property
    def fechaCalculo(self):
        return self.fecha_calculo

    def calcular_semaforo(self) -> str:
        """RN-008: Evalúa el cumplimiento respecto al esperado y asigna el color."""
        periodo = self.meta.periodo if self.meta else self.periodo
        if not periodo:
            self.semaforo = 'ROJO'
            return self.semaforo
        esperado = periodo.calcular_meta_esperada_al_dia()
        if esperado == 0:
            self.semaforo = 'VERDE'
            return self.semaforo
        porcentaje_relativo = (self.cumplimiento / esperado) * 100.0
        if porcentaje_relativo >= periodo.umbral_semaforo_verde:
            self.semaforo = 'VERDE'
        elif porcentaje_relativo >= periodo.umbral_semaforo_ambar:
            self.semaforo = 'AMBAR'
        else:
            self.semaforo = 'ROJO'
        return self.semaforo

    def calcularSemaforo(self) -> str:
        res = self.calcular_semaforo()
        return "AMARILLO" if res == "AMBAR" else res

    def actualizarAvance(self, nuevoAvance: float) -> bool:
        self.avance = nuevoAvance
        if self.meta and self.meta.valor_meta > 0:
            porcentaje = (self.avance / self.meta.valor_meta) * 100.0
            self.cumplimiento = round(porcentaje, 2)
        else:
            self.cumplimiento = 0.0
        self.calcular_semaforo()
        self.fecha_calculo = timezone.now().date()
        self.save(update_fields=['avance', 'cumplimiento', 'semaforo', 'fecha_calculo'])
        return True

    def clean(self):
        if self.ponderacion is not None and self.ponderacion < 0:
            raise ValidationError("La ponderación del indicador no puede ser negativa.")
        periodo = self.meta.periodo if self.meta_id else self.periodo
        if periodo and periodo.cerrado:
            raise ValidationError("RN-013: No se pueden alterar indicadores asociados a un período cerrado.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


# =============================================================================
# 15. CLASE AJUSTE INCENTIVO
# =============================================================================
class ConceptoAjuste(models.Model):
    class Naturaleza(models.TextChoices):
        POSITIVO = 'POSITIVO', 'Positivo'
        NEGATIVO = 'NEGATIVO', 'Negativo'

    nombre = models.CharField(max_length=100, unique=True)
    naturaleza = models.CharField(max_length=10, choices=Naturaleza.choices)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Concepto de ajuste'
        verbose_name_plural = 'Conceptos de ajuste'

    def __str__(self):
        return self.nombre


class AjusteIncentivo(models.Model):
    """
    Entidad AjusteIncentivo del modelo de dominio UML (Clase AjusteIncentivo).
    """
    TIPO_CHOICES = [
        ('INCENTIVO', 'Incentivo / Felicitación'),
        ('PENALIZACION', 'Penalización / Reclamo'),
        ('BONIFICACION', 'Bonificación'),
        ('DESCUENTO', 'Descuento'),
    ]

    tipo = models.CharField(max_length=100, blank=True, default='', verbose_name='Tipo de Ajuste')
    concepto = models.ForeignKey(
        ConceptoAjuste,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ajustes',
    )
    motivo = models.TextField(verbose_name='Motivo del Ajuste')
    valor_porcentaje = models.FloatField(default=0.0, verbose_name='Porcentaje de Ajuste (%)')
    fecha_registro = models.DateTimeField(default=timezone.now, verbose_name='Fecha de Registro')
    funcionario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='ajustes', verbose_name='Funcionario Evaluado')
    registrado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ajustes_registrados',
        verbose_name='Responsable del Registro',
    )
    periodo = models.ForeignKey(PeriodoMedicion, on_delete=models.CASCADE, null=True, blank=True, related_name='ajustes', verbose_name='Período Evaluativo')
    estado = models.CharField(max_length=20, default='PENDIENTE')
    aprobadoPor = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name='ajustes_aprobados')
    fechaAprobacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'AjustesIncentivo'
        verbose_name = 'Ajuste de Incentivo / Penalización'
        verbose_name_plural = 'Ajustes de Incentivos / Penalizaciones'

    def __str__(self):
        return f"{self.tipo} ({self.valor_porcentaje}%) - Funcionario: {self.funcionario}"

    def get_tipo_display(self):
        if self.concepto_id:
            return self.concepto.nombre
        return dict(self.TIPO_CHOICES).get(self.tipo, self.tipo)

    @property
    def es_negativo(self):
        if self.concepto_id:
            return self.concepto.naturaleza == ConceptoAjuste.Naturaleza.NEGATIVO
        return self.tipo in {'PENALIZACION', 'DESCUENTO'}

    @property
    def valorPorcentaje(self):
        return self.valor_porcentaje

    @valorPorcentaje.setter
    def valorPorcentaje(self, val):
        self.valor_porcentaje = val

    @property
    def fechaRegistro(self):
        return self.fecha_registro

    def aplicar_ajuste(self, cumplimiento_base: float = None) -> float:
        """RN-011 / RF-025: Calcula e impacta la variación porcentual."""
        impacto = abs(self.valor_porcentaje)
        if self.es_negativo:
            impacto = -impacto
        if cumplimiento_base is not None:
            if self.estado not in ["APROBADO", "APLICADO", "INCENTIVO", "PENALIZACION"]:
                return round(cumplimiento_base, 2)
            resultado = max(0.0, cumplimiento_base + impacto)
            if self.periodo and hasattr(self.periodo, 'tope_cumplimiento'):
                resultado = min(resultado, self.periodo.tope_cumplimiento)
            return round(resultado, 2)
        return impacto

    aplicarAjuste = aplicar_ajuste

    def aprobarAjuste(self, funcionarioAprobador) -> bool:
        if self.estado != "PENDIENTE":
            return False
        if self.periodo and self.periodo.cerrado:
            raise ValidationError("No se pueden aprobar ajustes de un período cerrado.")
        if not funcionarioAprobador.puede_aprobar_ajustes():
            raise PermissionError("El funcionario no cuenta con el rol o los permisos requeridos.")
        if self.funcionario_id == funcionarioAprobador.id:
            raise ValueError("Un funcionario no puede auto-aprobar su propio ajuste de incentivo.")
        if self.registrado_por_id and self.registrado_por_id == funcionarioAprobador.id:
            # CU-20: segregación de funciones (maker-checker).
            raise ValueError("Quien registró el ajuste no puede aprobarlo; debe decidirlo otra persona.")
        self.estado = "APROBADO"
        self.aprobadoPor = funcionarioAprobador
        self.fechaAprobacion = timezone.now()
        self.save(update_fields=['estado', 'aprobadoPor', 'fechaAprobacion'])
        return True

    def rechazarAjuste(self, funcionarioAprobador, motivoRechazo: str = None) -> bool:
        if self.estado != "PENDIENTE":
            return False
        if self.periodo and self.periodo.cerrado:
            raise ValidationError("No se pueden rechazar ajustes de un período cerrado.")
        if not funcionarioAprobador.puede_aprobar_ajustes():
            raise PermissionError("El funcionario no cuenta con los permisos requeridos para rechazar ajustes.")
        if self.funcionario_id == funcionarioAprobador.id:
            raise ValueError("Un funcionario no puede rechazar o evaluar su propio ajuste de incentivo.")
        if self.registrado_por_id and self.registrado_por_id == funcionarioAprobador.id:
            # CU-20: segregación de funciones (maker-checker).
            raise ValueError("Quien registró el ajuste no puede rechazarlo; debe decidirlo otra persona.")
        self.estado = "RECHAZADO"
        self.aprobadoPor = funcionarioAprobador
        self.fechaAprobacion = timezone.now()
        if motivoRechazo:
            self.motivo += f"\n[RECHAZADO]: {motivoRechazo}"
        self.save(update_fields=['estado', 'aprobadoPor', 'fechaAprobacion', 'motivo'])
        return True

    def clean(self):
        if self.periodo and self.periodo.cerrado:
            raise ValidationError("RN-013: No se pueden registrar o modificar ajustes dentro de un período cerrado.")
        if self.valor_porcentaje is not None and self.valor_porcentaje <= 0:
            raise ValidationError("El valor del porcentaje de ajuste debe ser un valor mayor a cero.")
        if not (self.motivo or '').strip():
            raise ValidationError({'motivo': 'El motivo del ajuste es obligatorio.'})
        if self.concepto_id:
            self.tipo = self.concepto.nombre
        elif self.tipo:
            concepto = ConceptoAjuste.objects.filter(
                nombre__iexact=self.get_tipo_display(),
                activo=True,
            ).first()
            if concepto:
                self.concepto = concepto


# =============================================================================
# CASOS SOCIALES
# =============================================================================
class CasoSocial(models.Model):
    codigo_caso = models.CharField(max_length=30, unique=True)
    persona_anonimizada = models.CharField(max_length=150)
    persona_clave = models.CharField(
        max_length=450,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    delegacion = models.ForeignKey(Delegacion, on_delete=models.CASCADE, related_name='casos_sociales')
    fecha_apertura = models.DateField(default=timezone.localdate)
    cerrado = models.BooleanField(default=False)

    def clean(self):
        super().clean()
        self.codigo_caso = (self.codigo_caso or '').strip()
        self.persona_anonimizada = (self.persona_anonimizada or '').strip()
        if not self.codigo_caso:
            raise ValidationError({'codigo_caso': 'El código del caso es obligatorio.'})
        if not self.persona_anonimizada:
            raise ValidationError({'persona_anonimizada': 'La identificación anonimizada es obligatoria.'})
        existente = None
        if self.pk:
            existente = type(self).objects.filter(pk=self.pk).values(
                'persona_anonimizada',
                'persona_clave',
            ).first()
        if (
            existente
            and existente['persona_anonimizada'].strip().casefold()
            == self.persona_anonimizada.casefold()
        ):
            self.persona_clave = existente['persona_clave']
        else:
            self.persona_clave = self.persona_anonimizada.casefold()
            duplicados = type(self).objects.filter(
                persona_anonimizada__iexact=self.persona_anonimizada,
            )
            if self.pk:
                duplicados = duplicados.exclude(pk=self.pk)
            if duplicados.exists():
                raise ValidationError({
                    'persona_anonimizada': (
                        'Ya existe un caso para esta identificación principal.'
                    )
                })
        if not self.delegacion_id:
            raise ValidationError({'delegacion': 'Selecciona una delegación válida.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def registrar_atencion(
        self,
        funcionario,
        tipo_atencion: str,
        detalle: str,
        resultado: str,
        fecha_gestion,
        tipo_atencion_catalogo=None,
        subatencion_catalogo=None,
    ):
        """RN-012: Registra una de las hasta 3 gestiones para este caso social."""
        with transaction.atomic():
            caso = type(self).objects.select_for_update().get(pk=self.pk)
            if caso.cerrado:
                raise ValueError("No se pueden registrar gestiones en un caso cerrado.")
            etapa_actual = (
                caso.atenciones.order_by('-etapa')
                .values_list('etapa', flat=True)
                .first()
                or 0
            ) + 1
            if etapa_actual > 3:
                raise ValueError(
                    "Un caso social no puede registrar más de 3 gestiones asociadas (RN-012)."
                )
            fecha_por_etapa = {
                1: {'fecha_visita_programada': fecha_gestion},
                2: {'fecha_visita': fecha_gestion},
                3: {'fecha_entrega_beneficio': fecha_gestion},
            }
            return AtencionSocial.objects.create(
                caso=caso,
                etapa=etapa_actual,
                funcionario=funcionario,
                tipo_atencion=(
                    tipo_atencion_catalogo.nombre
                    if tipo_atencion_catalogo
                    else tipo_atencion
                ),
                tipo_atencion_catalogo=tipo_atencion_catalogo,
                subatencion_catalogo=subatencion_catalogo,
                detalle=detalle,
                resultado=resultado,
                **fecha_por_etapa[etapa_actual],
            )

    registrarAtencion = registrar_atencion

    def cambiar_estado(self, cerrar: bool) -> bool:
        self.cerrado = cerrar
        self.save(update_fields=['cerrado'])
        return True

    cambiarEstado = cambiar_estado

    class Meta:
        verbose_name = 'Caso Social'
        verbose_name_plural = 'Casos Sociales'

    def __str__(self):
        return f"Caso Social {self.codigo_caso} - {self.persona_anonimizada}"


class AtencionSocial(models.Model):
    ETAPAS = (
        (1, 'Primera Gestión / Diagnóstico'),
        (2, 'Segunda Gestión / Seguimiento'),
        (3, 'Tercera Gestión / Cierre o Derivación'),
    )
    caso = models.ForeignKey(CasoSocial, on_delete=models.CASCADE, related_name='atenciones')
    etapa = models.PositiveSmallIntegerField(choices=ETAPAS)
    fecha = models.DateField(default=timezone.localdate)
    fecha_visita_programada = models.DateField(null=True, blank=True)
    fecha_visita = models.DateField(null=True, blank=True)
    fecha_entrega_beneficio = models.DateField(null=True, blank=True)
    tipo_atencion = models.CharField(max_length=100)
    tipo_atencion_catalogo = models.ForeignKey(
        TipoAtencionSocial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='atenciones',
    )
    tipo_atencion_area = models.CharField(max_length=100, blank=True, default='')
    subatencion = models.CharField(max_length=100, blank=True, default='')
    subatencion_catalogo = models.ForeignKey(
        SubatencionSocial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='atenciones',
    )
    detalle = models.TextField()
    resultado = models.CharField(max_length=150)
    funcionario = models.ForeignKey(Usuario, on_delete=models.CASCADE)

    def clean(self):
        super().clean()
        for field_name, label in (
            ('tipo_atencion', 'El tipo de atención'),
            ('detalle', 'El detalle'),
            ('resultado', 'El resultado'),
        ):
            value = (getattr(self, field_name) or '').strip()
            if not value:
                raise ValidationError({field_name: f'{label} es obligatorio.'})
            setattr(self, field_name, value)
        if not self.caso_id:
            raise ValidationError({'caso': 'Selecciona un caso social válido.'})
        if not self.funcionario_id:
            raise ValidationError({'funcionario': 'Selecciona un funcionario válido.'})
        if (
            self._state.adding
            and self.tipo_atencion_catalogo_id
            and self.funcionario.area_entidad_id
            and self.tipo_atencion_catalogo.area_id
            != self.funcionario.area_entidad_id
        ):
            raise ValidationError({
                'tipo_atencion_catalogo': (
                    'El tipo de atención debe pertenecer al área del funcionario.'
                )
            })
        if self.subatencion_catalogo_id and not self.tipo_atencion_catalogo_id:
            raise ValidationError({
                'tipo_atencion_catalogo': (
                    'Selecciona el tipo de atención asociado a la subatención.'
                )
            })
        if (
            self.subatencion_catalogo_id
            and self.subatencion_catalogo.tipo_atencion_id
            != self.tipo_atencion_catalogo_id
        ):
            raise ValidationError({
                'subatencion_catalogo': (
                    'La subatención debe pertenecer al tipo de atención seleccionado.'
                )
            })
        fecha_por_etapa = {
            1: ('fecha_visita_programada', 'La fecha programada de visita'),
            2: ('fecha_visita', 'La fecha de visita realizada'),
            3: ('fecha_entrega_beneficio', 'La fecha de entrega del beneficio'),
        }
        campo_fecha, etiqueta_fecha = fecha_por_etapa.get(
            self.etapa,
            (None, None),
        )
        if not campo_fecha:
            raise ValidationError({'etapa': 'Selecciona una etapa válida.'})
        fecha_gestion = getattr(self, campo_fecha)
        if not fecha_gestion:
            raise ValidationError({
                campo_fecha: f'{etiqueta_fecha} es obligatoria para esta gestión.'
            })
        if self.etapa == 1 and fecha_gestion < timezone.localdate():
            raise ValidationError({
                campo_fecha: 'La fecha programada de visita no puede ser anterior a hoy.'
            })
        if self.etapa in (2, 3) and fecha_gestion > timezone.localdate():
            raise ValidationError({
                campo_fecha: 'La fecha de una gestión realizada no puede ser futura.'
            })

    def save(self, *args, **kwargs):
        if self._state.adding and self.tipo_atencion_catalogo_id:
            self.tipo_atencion = self.tipo_atencion_catalogo.nombre
            self.tipo_atencion_area = self.tipo_atencion_catalogo.area.nombre
        if self._state.adding and self.subatencion_catalogo_id:
            self.subatencion = self.subatencion_catalogo.nombre
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        unique_together = ('caso', 'etapa')
        ordering = ['caso', 'etapa']
        verbose_name = 'Atención Social'
        verbose_name_plural = 'Atenciones Sociales'

    def __str__(self):
        return f"{self.caso.codigo_caso} - Etapa {self.etapa}"


# =============================================================================
# 6. CLASE AUDITORIA / REGISTROAUDITORIA
# =============================================================================
def _registrar_intento_alteracion_auditoria(operacion, identificadores):
    """HU-30 / CU-18: el intento de alterar la auditoría se deniega y queda registrado.

    Se escribe en el log de seguridad y como evento de auditoría. Si el intento
    ocurre dentro de una transacción que luego se revierte, el log se conserva.
    """
    from sgr_app.services.contexto import usuario_actual

    usuario = usuario_actual()
    identificadores = [str(identificador) for identificador in identificadores][:20]
    logging.getLogger('sgr.security').warning(
        'Intento de alterar la auditoría: operacion=%s registros=%s usuario=%r',
        operacion, identificadores, getattr(usuario, 'username', None),
    )
    try:
        RegistroAuditoria(
            usuario=usuario,
            funcionario=usuario,
            evento='INTENTO_ALTERACION',
            accion='INTENTO_ALTERAR_AUDITORIA',
            entidad='RegistroAuditoria',
            identificador=(identificadores[0] if identificadores else '0')[:50],
            identificador_registro=(identificadores[0] if identificadores else '0')[:50],
            valor_anterior=None,
            valor_nuevo={'operacion': operacion, 'registros': identificadores},
        ).save()
    except Exception:
        logging.getLogger('sgr.security').exception('No se pudo registrar el intento de alteración.')


class RegistroAuditoriaQuerySet(models.QuerySet):
    def update(self, **kwargs):
        _registrar_intento_alteracion_auditoria('UPDATE', self.values_list('pk', flat=True))
        raise ValidationError('Los registros de auditoría son inmutables.')

    def delete(self):
        _registrar_intento_alteracion_auditoria('DELETE', self.values_list('pk', flat=True))
        raise ValidationError('Los registros de auditoría no se pueden eliminar.')


class RegistroAuditoria(models.Model):
    """
    Entidad del sistema encargada de registrar la trazabilidad histórica (Clase Auditoria).
    """
    EVENTO_CHOICES = [
        ('CREACION', 'Creación de Registro'),
        ('MODIFICACION', 'Modificación de Datos'),
        ('ELIMINACION', 'Eliminación de Registro'),
        ('LOGIN', 'Inicio de Sesión'),
        ('APROBACION', 'Aprobación / Validación'),
    ]

    evento = models.CharField(max_length=50, default='CREACION', verbose_name="Tipo de Evento")
    accion = models.CharField(max_length=50, default='CREACION')
    fecha_auditoria = models.DateTimeField(default=timezone.now, blank=True, verbose_name="Fecha y Hora de Auditoría")
    fecha = models.DateTimeField(auto_now_add=True)
    entidad = models.CharField(max_length=100, verbose_name="Entidad / Tabla Afectada")
    identificador = models.CharField(max_length=50, default='0', verbose_name="Identificador del Registro (ID)")
    identificador_registro = models.CharField(max_length=50, default='0')
    valor_anterior = models.JSONField(blank=True, null=True, verbose_name="Valores Anteriores (JSON)")
    valor_nuevo = models.JSONField(blank=True, null=True, verbose_name="Valores Nuevos (JSON)")
    funcionario = models.ForeignKey(Usuario, on_delete=models.PROTECT, null=True, blank=True, related_name='auditorias', verbose_name="Funcionario Responsable")
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, null=True, blank=True, related_name='auditorias_legacy')
    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    objects = RegistroAuditoriaQuerySet.as_manager()

    class Meta:
        db_table = 'Auditorias'
        verbose_name = 'Auditoría'
        verbose_name_plural = 'Auditorías'
        ordering = ['-fecha_auditoria']

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            _registrar_intento_alteracion_auditoria('UPDATE', [self.pk])
            raise ValidationError('Los registros de auditoría son inmutables.')
        self._normalizar_campos_obligatorios()
        super().save(*args, **kwargs)

    def _normalizar_campos_obligatorios(self):
        """
        RNF-008: unifica los campos duplicados heredados (usuario/funcionario,
        identificador/identificador_registro, evento/accion) y exige los datos
        mínimos de cada evento.
        """
        if self.funcionario_id and not self.usuario_id:
            self.usuario_id = self.funcionario_id
        elif self.usuario_id and not self.funcionario_id:
            self.funcionario_id = self.usuario_id
        if self.identificador in ('', '0', None) and self.identificador_registro not in ('', '0', None):
            self.identificador = self.identificador_registro
        elif self.identificador_registro in ('', '0', None) and self.identificador not in ('', '0', None):
            self.identificador_registro = self.identificador
        if not (self.accion or '').strip():
            self.accion = self.evento
        if not (self.entidad or '').strip() or not (self.accion or '').strip():
            raise ValidationError('Todo evento de auditoría debe indicar la acción y la entidad afectada.')
        if not self.funcionario_id:
            # Eventos de procesos automáticos (comandos programados) no tienen
            # usuario; quedan identificados como "Sistema" y se registran en el log.
            logging.getLogger('sgr.security').info(
                'Evento de auditoría sin usuario (sistema): accion=%s entidad=%s',
                self.accion, self.entidad,
            )

    def delete(self, *args, **kwargs):
        _registrar_intento_alteracion_auditoria('DELETE', [self.pk])
        raise ValidationError('Los registros de auditoría no se pueden eliminar.')

    def __str__(self):
        func_nom = self.funcionario.get_full_name() if self.funcionario else (self.usuario.username if self.usuario else "Sistema")
        return f"Auditoría #{self.id} - {self.evento} en {self.entidad} por {func_nom}"

    @classmethod
    def registrar_auditoria(cls, funcionario_obj, evento, entidad, identificador, valor_anterior=None, valor_nuevo=None):
        return cls.objects.create(
            funcionario=funcionario_obj if isinstance(funcionario_obj, Usuario) else None,
            usuario=funcionario_obj if isinstance(funcionario_obj, Usuario) else None,
            evento=evento,
            accion=evento,
            entidad=entidad,
            identificador=str(identificador),
            identificador_registro=str(identificador),
            valor_anterior=valor_anterior,
            valor_nuevo=valor_nuevo
        )

    @classmethod
    def registrarAuditoria(cls, evento: str, entidad: str, identificador: str, valor_anterior: dict = None, valor_nuevo: dict = None, funcionario=None):
        return cls.registrar_auditoria(funcionario, evento, entidad, identificador, valor_anterior, valor_nuevo)

    @classmethod
    def consultarHistorial(cls, entidad: str, identificador: str):
        return cls.objects.filter(
            entidad__iexact=entidad,
            identificador=str(identificador)
        ).order_by('-fecha_auditoria')


# =============================================================================
# ALIASES OFICIALES PARA EQUIVALENCIA TOTAL 1:1 CON LOS MODELOS DE SOLEDAD
# =============================================================================
Funcionario = Usuario
Compromiso = CompromisoAgenda
Periodo = PeriodoMedicion
Item = ItemMedicion
MetaModel = Meta
Auditoria = RegistroAuditoria


# =============================================================================
# REGLAS Y SEGUIMIENTO DE ALERTAS OPERATIVAS (HU-31, CU-07 y CU-37)
# =============================================================================
class ReglaAlerta(models.Model):
    """Versión de una regla de alerta (CU-07).

    Por normativa no se edita ni se borra: cada cambio crea una versión nueva con
    su vigencia; la vigente es la última con `vigente_desde` <= ahora.
    """

    class Tipo(models.TextChoices):
        VENCIMIENTO = 'VENCIMIENTO', 'Vencimiento de compromisos'
        PLAZO_PROXIMO = 'PLAZO_PROXIMO', 'Plazo próximo'
        EVIDENCIA_PENDIENTE = 'EVIDENCIA_PENDIENTE', 'Evidencias pendientes'
        RENDIMIENTO_BAJO = 'RENDIMIENTO_BAJO', 'Rendimiento bajo'
        INACTIVIDAD = 'INACTIVIDAD', 'Inactividad'

    DESTINATARIOS = (
        ('FUNCIONARIO', 'Funcionario'),
        ('DELEGADO', 'Delegado'),
        ('COORDINADOR', 'Coordinador'),
        ('ADMINISTRADOR', 'Administrador'),
    )

    tipo = models.CharField(max_length=30, choices=Tipo.choices, db_index=True)
    activa = models.BooleanField(default=True)
    umbral = models.FloatField(null=True, blank=True)
    destinatarios = models.JSONField(default=list)
    vigente_desde = models.DateTimeField(default=timezone.now)
    creada_por = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, null=True, blank=True, related_name='reglas_alerta',
    )
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tipo', '-vigente_desde', '-pk']
        verbose_name = 'Regla de alerta'
        verbose_name_plural = 'Reglas de alerta'

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError('Las reglas de alerta no se modifican: registra una nueva versión.')
        super().save(*args, **kwargs)


class AlertaGenerada(models.Model):
    """Alerta ya generada y la regla con que se generó (CU-07).

    "Un cambio de regla no altera las alertas ya generadas": mientras su condición
    siga abierta, la alerta se evalúa con la regla guardada aquí y no con la
    vigente. Las situaciones nuevas usan la regla vigente. No se edita ni se borra.
    """

    clave = models.CharField(max_length=200, unique=True)
    tipo = models.CharField(max_length=30, db_index=True)
    regla = models.ForeignKey(
        ReglaAlerta, on_delete=models.PROTECT, null=True, blank=True, related_name='alertas_generadas',
        help_text='Versión de la regla usada; vacía si se usaron los valores por defecto.',
    )
    regla_activa = models.BooleanField(default=True)
    regla_umbral = models.FloatField(null=True, blank=True)
    regla_destinatarios = models.JSONField(default=list)
    generada_en = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Alerta generada'
        verbose_name_plural = 'Alertas generadas'

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError('Una alerta generada no se modifica.')
        super().save(*args, **kwargs)


class AlertaRevisada(models.Model):
    """Marca de revisión de una alerta (CU-37). La alerta original no se elimina."""

    clave = models.CharField(max_length=200, unique=True)
    tipo = models.CharField(max_length=30)
    revisada_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name='alertas_revisadas')
    revisada_en = models.DateTimeField(default=timezone.now)
    comentario = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        verbose_name = 'Alerta revisada'
        verbose_name_plural = 'Alertas revisadas'
