# Matriz SGR — aplicación Django

Aplicación web y API del Sistema de Gestión de Resultados de la Municipalidad de La Serena. Incluye registro de actividades, evidencias, compromisos, casos sociales, auditoría y seguimiento trimestral mediante semáforo.

## Requisitos

- Python 3.12 o superior.
- Windows, macOS o Linux.
- SQLite para desarrollo local (incluido con Python).
- MySQL/MariaDB solo si se requiere compartir datos con phpMyAdmin/WAMP.

## Instalación local con SQLite

Desde la raíz del repositorio:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

En macOS/Linux, sustituye la creación/activación del entorno virtual por:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Genera una clave privada para Django:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Edita `backend/.env`: reemplaza `SECRET_KEY` por la clave generada. Para uso local conserva `DEBUG=True` y `USE_MYSQL=False`. El archivo `.env` contiene configuración privada y no se debe subir a GitHub.

Aplica las migraciones, crea una cuenta administradora y arranca el servidor:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Abre <http://127.0.0.1:8000/> para iniciar sesión en la aplicación SGR.

> En PowerShell, si no permite activar scripts, abre una terminal nueva o ejecuta `Set-ExecutionPolicy -Scope Process RemoteSigned` en esa sesión.

### Datos de demostración (opcional)

`python manage.py poblar_sgr` agrega delegaciones, cargos, cuentas de demostración y datos sintéticos. Úsalo solo en una base local desechable: **no lo ejecutes sobre una base que ya contenga información real**. El comando no establece contraseñas conocidas; asígnalas individualmente con `python manage.py changepassword <usuario>`.

## Conectar a MySQL/MariaDB de WAMP

phpMyAdmin administra el servidor MySQL/MariaDB; Django se conecta directamente al servidor de base de datos.

1. Inicia WAMP y crea la base `matriz_sgr_db` con cotejamiento `utf8mb4_unicode_ci`.
2. Crea un usuario MySQL dedicado con permisos únicamente sobre esa base. No uses `root` ni expongas phpMyAdmin fuera de tu equipo.
3. En `backend/.env`, configura `USE_MYSQL=True`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` y `DB_PORT`. No copies credenciales al repositorio.
4. Aplica `python manage.py migrate` desde `backend/`.
5. Si es una base nueva y desechable, crea el administrador con `python manage.py createsuperuser`. Ejecuta el seeder solo si también quieres datos sintéticos.

`USE_MYSQL=False` usa `backend/db.sqlite3`; activar MySQL no copia ni combina datos de SQLite.

## Pruebas y verificaciones

Para que las pruebas no se conecten a la base MySQL configurada en `.env`, fuerza SQLite en la terminal:

```powershell
$env:USE_MYSQL = "False"
$env:DEBUG = "True"
$env:SECURE_SSL_REDIRECT = "False"
python manage.py test sgr_app
python manage.py check
python manage.py makemigrations --check --dry-run
```

Para inspeccionar alertas de configuración de producción:

```powershell
python manage.py check --deploy
```

Configura antes de publicar `DEBUG=False`, una clave secreta nueva, `ALLOWED_HOSTS`, HTTPS, cookies seguras, HSTS, orígenes CSRF/CORS y SMTP. Ajusta la configuración HTTPS si la aplicación está detrás de un proxy inverso.

## Variables de entorno

| Variable | Uso |
| --- | --- |
| `SECRET_KEY` | Clave privada de Django; obligatoria. |
| `DEBUG` | Depuración; `True` solo en desarrollo local. |
| `ALLOWED_HOSTS` | Lista separada por comas de nombres de host permitidos. |
| `USE_MYSQL` | `False` para SQLite; `True` para MySQL/MariaDB. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexión a MySQL/MariaDB. |
| `USE_S3` | Activa almacenamiento de evidencias en Amazon S3. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME` | Credenciales y bucket S3; configura permisos mínimos. |
| `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` | Orígenes explícitos permitidos, separados por comas. |
| `EMAIL_BACKEND` | Consola en desarrollo (`DEBUG=True`) y SMTP en producción por defecto; se puede sobrescribir. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL` | Configuración SMTP para enviar correo fuera del entorno local. |
| `DEFAULT_FROM_EMAIL` | Dirección remitente de correos del SGR. |

## Funcionalidad principal

- Actividad diaria con ubicación GPS, dirección y compromiso vinculado.
- Evidencias fotográficas asociadas a actividades, con validación por rol.
- Casos sociales con acceso limitado por rol y delegación.
- Semáforo trimestral con umbrales configurables en la base de datos.
- API REST autenticada bajo `/api/`.
