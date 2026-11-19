"""Configuración del proyecto web Matriz SGR."""

import sys
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from decouple import AutoConfig, Csv

# Rutas y variables de entorno; el archivo backend/.env no se versiona.
BASE_DIR = Path(__file__).resolve().parent.parent
env = AutoConfig(search_path=BASE_DIR)

# La clave debe configurarse fuera del código fuente.
SECRET_KEY = env('SECRET_KEY', default='')
if not SECRET_KEY:
    raise ImproperlyConfigured(
        'Configura SECRET_KEY en backend/.env o como variable de entorno.'
    )

# DEBUG debe estar desactivado en cualquier entorno publicado.
DEBUG = env('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = env(
    'ALLOWED_HOSTS',
    default='127.0.0.1,localhost',
    cast=Csv(),
)
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

EVIDENCE_MAX_FILE_SIZE = env(
    'EVIDENCE_MAX_FILE_SIZE',
    default=5 * 1024 * 1024,
    cast=int,
)
EVIDENCE_MAX_IMAGE_PIXELS = env(
    'EVIDENCE_MAX_IMAGE_PIXELS',
    default=40_000_000,
    cast=int,
)
EVIDENCE_ALLOWED_FORMATS = tuple(
    formato.upper()
    for formato in env(
        'EVIDENCE_ALLOWED_FORMATS',
        default='JPEG,PNG,WEBP',
        cast=Csv(),
    )
)
EVIDENCE_RETENTION_DAYS = env(
    'EVIDENCE_RETENTION_DAYS',
    default=365,
    cast=int,
)
if (
    EVIDENCE_MAX_FILE_SIZE <= 0
    or EVIDENCE_MAX_IMAGE_PIXELS <= 0
    or EVIDENCE_RETENTION_DAYS <= 0
):
    raise ImproperlyConfigured(
        'Los límites de tamaño, resolución y retención de evidencia deben ser mayores que cero.'
    )
if not EVIDENCE_ALLOWED_FORMATS or not set(EVIDENCE_ALLOWED_FORMATS).issubset(
    {'JPEG', 'PNG', 'WEBP'}
):
    raise ImproperlyConfigured(
        'EVIDENCE_ALLOWED_FORMATS solo puede contener JPEG, PNG y WEBP.'
    )


# Aplicaciones instaladas y middleware.

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'corsheaders',
    'sgr_app',
]

# S3 se instala solo cuando se activa el almacenamiento en la nube.
USE_S3 = env('USE_S3', default=False, cast=bool)
if USE_S3:
    INSTALLED_APPS.append('storages')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'sgr_app.dos_factores.Exigir2FAMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'sgr_app.middleware.ContentSecurityPolicyMiddleware',
    'sgr_app.middleware.AuditarAccesoDenegadoMiddleware',
    'sgr_app.middleware.SoloLecturaConsultaMiddleware',
]

ROOT_URLCONF = 'sgr_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.csrf',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'sgr_app.context_processors.sgr_global_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'sgr_backend.wsgi.application'


# SQLite se usa por defecto; MySQL/MariaDB se activa explícitamente.
USE_MYSQL = env('USE_MYSQL', default=False, cast=bool)

if USE_MYSQL:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': env('DB_NAME'),
            'USER': env('DB_USER'),
            'PASSWORD': env('DB_PASSWORD', default=''),
            'HOST': env('DB_HOST', default='127.0.0.1'),
            'PORT': env('DB_PORT', default='3306'),
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            }
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


# Validadores de contraseñas para las cuentas de usuario.

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Solo al ejecutar `manage.py test`: hash rápido para acelerar las pruebas.
# PBKDF2 es lento a propósito (protege contra fuerza bruta) y las pruebas crean
# cientos de usuarios. Nunca se aplica al servidor ni a otros comandos.
TESTING = len(sys.argv) > 1 and sys.argv[1] == 'test'
if TESTING:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

LOGIN_REDIRECT_URL = '/dashboard/'
LOGIN_URL = '/login/'


# Idioma, zona horaria e internacionalización.
LANGUAGE_CODE = 'es-cl'

TIME_ZONE = 'America/Santiago'

USE_I18N = True

USE_TZ = True


# Archivos estáticos (CSS, JavaScript e imágenes).
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Evidencias en almacenamiento local o en Amazon S3.
if USE_S3:
    AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='').strip() or None
    AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='').strip() or None
    AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='')
    AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='us-east-1')
    AWS_S3_CUSTOM_DOMAIN = env(
        'AWS_S3_CUSTOM_DOMAIN',
        default='',
    ).strip() or None
    if not AWS_STORAGE_BUCKET_NAME:
        raise ImproperlyConfigured(
            'Configura AWS_STORAGE_BUCKET_NAME cuando USE_S3=True.'
        )
    AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
    else:
        MEDIA_URL = (
            f'https://{AWS_STORAGE_BUCKET_NAME}.s3.'
            f'{AWS_S3_REGION_NAME}.amazonaws.com/'
        )
else:
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'

AUTH_USER_MODEL = 'sgr_app.Usuario'

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env('CORS_ALLOWED_ORIGINS', default='', cast=Csv())
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

# En producción se asume HTTPS; se pueden ajustar estos valores tras el proxy.
SECURE_SSL_REDIRECT = env('SECURE_SSL_REDIRECT', default=not DEBUG, cast=bool)
SESSION_COOKIE_SECURE = env('SESSION_COOKIE_SECURE', default=not DEBUG, cast=bool)
CSRF_COOKIE_SECURE = env('CSRF_COOKIE_SECURE', default=not DEBUG, cast=bool)
if env('TRUST_PROXY_SSL_HEADER', default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = env(
    'SECURE_HSTS_SECONDS',
    default=31536000 if not DEBUG else 0,
    cast=int,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env(
    'SECURE_HSTS_INCLUDE_SUBDOMAINS',
    default=False,
    cast=bool,
)
SECURE_HSTS_PRELOAD = env('SECURE_HSTS_PRELOAD', default=False, cast=bool)

# Decisiones deliberadas en `check --deploy`: los subdominios del dominio
# municipal y su inclusión en la lista de precarga HSTS no dependen de este
# proyecto. Actívalos con las variables anteriores cuando el dominio lo permita.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        # Solo sesión: BasicAuthentication enviaría credenciales en cada petición.
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'sgr_app.permissions.AuthenticatedRolePermission',
    ],
    # HU-26: los 403 de la API quedan en la auditoría.
    'EXCEPTION_HANDLER': 'sgr_app.services.auditoria_acceso.exception_handler_api',
    # RNF-016: contrato OpenAPI 3 generado desde el código.
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'API Matriz SGR - Ilustre Municipalidad de La Serena',
    'DESCRIPTION': (
        'API REST del Sistema de Gestión de Resultados. Autenticación por sesión '
        '(POST /api/v1/auth/login/). Las escrituras admiten control de concurrencia '
        'con las cabeceras ETag / If-Match (412 si el registro cambió).'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SERVE_PERMISSIONS': ['rest_framework.permissions.IsAuthenticated'],
    'PREPROCESSING_HOOKS': ['sgr_app.openapi.solo_api_v1'],
    'COMPONENT_SPLIT_REQUEST': True,
    # Versión fija de Swagger UI (no "@latest").
    'SWAGGER_UI_DIST': 'https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14',
    'SWAGGER_UI_FAVICON_HREF': 'https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/favicon-32x32.png',
}


# Correo: consola durante desarrollo y SMTP en producción.
EMAIL_BACKEND = env('EMAIL_BACKEND', default='').strip() or (
        'django.core.mail.backends.console.EmailBackend'
        if DEBUG else 'django.core.mail.backends.smtp.EmailBackend'
    )
EMAIL_HOST = env('EMAIL_HOST', default='localhost')
EMAIL_PORT = env('EMAIL_PORT', default=25, cast=int)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env('EMAIL_USE_TLS', default=False, cast=bool)
EMAIL_USE_SSL = env('EMAIL_USE_SSL', default=False, cast=bool)
DEFAULT_FROM_EMAIL = env(
    'DEFAULT_FROM_EMAIL',
    default=EMAIL_HOST_USER or 'sgr@laserena.cl',
)


# =========================================================================
# Controles de seguridad adicionales (OWASP A05, A07, A09)
# =========================================================================

# Número de proxies de confianza delante de Django (ALB + Nginx = 2).
# Con 0 se usa REMOTE_ADDR. Necesario para que el bloqueo por IP use la IP real.
TRUSTED_PROXY_COUNT = env('TRUSTED_PROXY_COUNT', default=0, cast=int)

# Bloqueo temporal ante intentos fallidos de inicio de sesión y 2FA.
LOGIN_MAX_FAILED_ATTEMPTS = env('LOGIN_MAX_FAILED_ATTEMPTS', default=5, cast=int)
LOGIN_MAX_FAILED_ATTEMPTS_PER_IP = env('LOGIN_MAX_FAILED_ATTEMPTS_PER_IP', default=30, cast=int)
# Roles que deben activar verificación en dos pasos (RNF-004), p. ej.
# "ADMINISTRADOR,COORDINADOR,DELEGADO". Vacío = opcional para todos.
TWO_FACTOR_REQUIRED_ROLES = env('TWO_FACTOR_REQUIRED_ROLES', default='', cast=Csv())
TWO_FACTOR_MAX_FAILED_ATTEMPTS = env('TWO_FACTOR_MAX_FAILED_ATTEMPTS', default=5, cast=int)
LOGIN_LOCKOUT_MINUTES = env('LOGIN_LOCKOUT_MINUTES', default=15, cast=int)
if TRUSTED_PROXY_COUNT < 0 or min(
    LOGIN_MAX_FAILED_ATTEMPTS,
    LOGIN_MAX_FAILED_ATTEMPTS_PER_IP,
    TWO_FACTOR_MAX_FAILED_ATTEMPTS,
    LOGIN_LOCKOUT_MINUTES,
) <= 0:
    raise ImproperlyConfigured(
        'Los límites de intentos y el tiempo de bloqueo deben ser mayores que cero.'
    )

# Sesiones: duración máxima y cierre al cerrar el navegador.
SESSION_COOKIE_AGE = env('SESSION_COOKIE_AGE', default=8 * 60 * 60, cast=int)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env(
    'SESSION_EXPIRE_AT_BROWSER_CLOSE',
    default=True,
    cast=bool,
)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

# Content-Security-Policy. Las plantillas actuales usan scripts y estilos en
# línea, por eso se permite 'unsafe-inline'; el resto de orígenes queda acotado.
# CSP_REPORT_ONLY=True solo informa violaciones en la consola del navegador.
CSP_REPORT_ONLY = env('CSP_REPORT_ONLY', default=True, cast=bool)
_CSP_MEDIA_ORIGIN = MEDIA_URL.rstrip('/') if MEDIA_URL.startswith('https://') else ''
CONTENT_SECURITY_POLICY = {
    'default-src': ["'self'"],
    'script-src': ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net', 'https://unpkg.com'],
    'style-src': [
        "'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net',
        'https://unpkg.com', 'https://fonts.googleapis.com',
    ],
    'font-src': ["'self'", 'data:', 'https://cdn.jsdelivr.net', 'https://fonts.gstatic.com'],
    'img-src': [
        "'self'", 'data:', 'blob:', 'https://unpkg.com',
        'https://server.arcgisonline.com',
        *([_CSP_MEDIA_ORIGIN] if _CSP_MEDIA_ORIGIN else []),
    ],
    'connect-src': ["'self'"],
    'object-src': ["'none'"],
    'base-uri': ["'self'"],
    'form-action': ["'self'"],
    'frame-ancestors': ["'none'"],
}

# =========================================================================
# Operación: alertas, monitoreo, disponibilidad y respaldos (RNF-001/010/018)
# =========================================================================
ALERTAS_EMAIL = env('ALERTAS_EMAIL', default='', cast=Csv())
# Errores 500 de Django se envían por correo a estas direcciones.
ADMINS = [(correo, correo) for correo in ALERTAS_EMAIL]
SERVER_EMAIL = DEFAULT_FROM_EMAIL

MONITOR_URL = env('MONITOR_URL', default='http://127.0.0.1:8000/health/?profundo=1')
MONITOR_HOST = env('MONITOR_HOST', default='')
MONITOR_CA_FILE = env('MONITOR_CA_FILE', default='')
MONITOR_TIMEOUT = env('MONITOR_TIMEOUT', default=10, cast=int)
MONITOR_STATE_FILE = (
    env('MONITOR_STATE_FILE', default='').strip() or str(BASE_DIR / '.monitor_estado.json')
)
MONITOR_GAP_SECONDS = env('MONITOR_GAP_SECONDS', default=300, cast=int)
MONITOR_DISK_ALERT_PCT = env('MONITOR_DISK_ALERT_PCT', default=85, cast=float)
MONITOR_MEM_ALERT_PCT = env('MONITOR_MEM_ALERT_PCT', default=10, cast=float)

# Jornada operativa para el cálculo de disponibilidad (0 = lunes).
JORNADA_DIAS = env('JORNADA_DIAS', default='0,1,2,3,4')
JORNADA_HORAS = env('JORNADA_HORAS', default='08:00-18:00')
DISPONIBILIDAD_META_PCT = env('DISPONIBILIDAD_META_PCT', default=99.5, cast=float)

BACKUP_DIR = env('BACKUP_DIR', default='').strip() or str(BASE_DIR / 'respaldos')
BACKUP_LOCAL_DAYS = env('BACKUP_LOCAL_DAYS', default=7, cast=int)
BACKUP_S3_BUCKET = env(
    'BACKUP_S3_BUCKET',
    default=env('AWS_STORAGE_BUCKET_NAME', default='') if USE_S3 else '',
)
BACKUP_S3_PREFIX = env('BACKUP_S3_PREFIX', default='respaldos/')

# Registro técnico (OWASP A09). En producción systemd/journald captura la
# consola; LOG_FILE permite además un archivo rotativo.
LOG_LEVEL = env('LOG_LEVEL', default='INFO')
LOG_FILE = env('LOG_FILE', default='').strip()
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'excluir_health': {'()': 'sgr_app.logging_filters.ExcluirHealthCheck'},
        'sin_traceback_4xx': {'()': 'sgr_app.logging_filters.SinTracebackEnErroresCliente'},
    },
    'formatters': {
        'detallado': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'detallado',
        },
        'correo_admins': {
            'class': 'django.utils.log.AdminEmailHandler',
            'level': 'ERROR',
            'include_html': False,
            'filters': ['excluir_health'],
        },
        **({
            'archivo': {
                'class': 'logging.handlers.RotatingFileHandler',
                'filename': LOG_FILE,
                'maxBytes': 5 * 1024 * 1024,
                'backupCount': 10,
                'encoding': 'utf-8',
                'formatter': 'detallado',
            },
        } if LOG_FILE else {}),
    },
    'loggers': {
        # Errores 4xx/5xx de peticiones y de seguridad de Django (p. ej. CSRF).
        # Los 500 se envían además por correo a ALERTAS_EMAIL (RNF-018).
        'django.request': {
            'handlers': [
                'console',
                *(['archivo'] if LOG_FILE else []),
                *(['correo_admins'] if ALERTAS_EMAIL else []),
            ],
            'level': 'WARNING',
            'filters': ['sin_traceback_4xx'],
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console', *(['archivo'] if LOG_FILE else [])],
            'level': 'WARNING',
            'propagate': False,
        },
        # Autenticación: inicios/cierres de sesión, fallos y bloqueos.
        'sgr.security': {
            'handlers': ['console', *(['archivo'] if LOG_FILE else [])],
            'level': 'WARNING' if TESTING else LOG_LEVEL,
            'propagate': False,
        },
        # Monitoreo, respaldos y alertas.
        'sgr.operacion': {
            'handlers': ['console', *(['archivo'] if LOG_FILE else [])],
            'level': 'ERROR' if TESTING else LOG_LEVEL,
            'propagate': False,
        },
    },
}
