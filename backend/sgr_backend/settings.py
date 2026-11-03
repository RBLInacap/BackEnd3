"""Configuración del proyecto web Matriz SGR."""

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
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
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

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'sgr_app.permissions.AuthenticatedRolePermission',
    ],
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
