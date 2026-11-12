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

### Alta y primer acceso de funcionarios

Al crear un funcionario desde el mantenedor, su usuario es el correo electrónico
y su contraseña temporal es el RUN sin puntos ni guion. Debe iniciar sesión
dentro de los 10 minutos siguientes a la creación. En el primer acceso se solicita
ingresar y confirmar una contraseña nueva antes de entrar al sistema. Si el plazo
vence, el administrador puede usar «Renovar acceso» en el mantenedor de
funcionarios para iniciar un nuevo plazo de 10 minutos.

El mantenedor de funcionarios permite asignar varios roles adicionales al rol
principal. Los permisos efectivos son la unión de los permisos de esos roles;
se configuran desde el mantenedor de roles. Los roles de gestión territorial
requieren una delegación y limitan el acceso a ella, salvo que también se
otorguen permisos de coordinación o administración.

### Recuperación de contraseña por código de un solo uso

En `/password-reset/`, el funcionario ingresa el correo de su cuenta y recibe un
código de verificación. En la pantalla siguiente ingresa únicamente ese código,
sin volver a escribir el correo. El código vence en 10 minutos, solo se puede
usar una vez y permite como máximo cinco intentos. Tras validarlo, debe ingresar
y confirmar su nueva contraseña. Las solicitudes se limitan a un código por
minuto y la respuesta no revela si el correo está registrado.

En desarrollo, Django imprime el mensaje en la consola cuando `DEBUG=True` y
`EMAIL_BACKEND` está vacío. Para enviar el código por Gmail, crea una contraseña
de aplicación en la cuenta remitente:
Google requiere tener activada la verificación en dos pasos. Usa esa contraseña de
aplicación, no la contraseña normal de Gmail.

Configura estas variables en `backend/.env`, que está excluido de Git:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=tu-cuenta@gmail.com
EMAIL_HOST_PASSWORD=tu_contrasena_de_aplicacion
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=tu-cuenta@gmail.com
```

Reinicia el servidor después de cambiar la configuración. Para comprobar SMTP,
ejecuta desde `backend/` y reemplaza la dirección de destino por una cuenta que
puedas revisar:

```powershell
python manage.py shell -c "from django.core.mail import send_mail; print(send_mail('Prueba SMTP SGR', 'Correo de prueba.', None, ['tu-correo-de-prueba@gmail.com']))"
```

Un resultado `1` significa que el servidor SMTP aceptó el mensaje para envío;
confirma su recepción en la bandeja de entrada o spam. Luego prueba el formulario
`/password-reset/` con un usuario activo cuyo correo registrado puedas consultar.
Nunca subas `backend/.env` ni compartas la contraseña de aplicación.

### Datos de demostración (opcional)

`python manage.py poblar_sgr` agrega delegaciones, cargos, cuentas de demostración y datos sintéticos. Úsalo solo en una base local desechable: **no lo ejecutes sobre una base que ya contenga información real**. El comando no establece contraseñas conocidas; asígnalas individualmente con `python manage.py changepassword <usuario>`.

### Política de evidencias

Las evidencias aceptan imágenes JPEG, PNG y WEBP de hasta 5 MiB y 40 megapíxeles por archivo. El sistema verifica el contenido real de la imagen, no solo el MIME enviado por el navegador, y registra nombre original, formato, tamaño, fecha y usuario que la cargó. Estos valores se pueden ajustar con `EVIDENCE_MAX_FILE_SIZE` (bytes), `EVIDENCE_MAX_IMAGE_PIXELS`, `EVIDENCE_ALLOWED_FORMATS` y `EVIDENCE_RETENTION_DAYS` en `backend/.env`.

La retención predeterminada es de 365 días. Programa diariamente `python manage.py purgar_evidencias_vencidas` mediante el Programador de tareas de Windows o cron. Al vencer el plazo, se eliminan los archivos almacenados y se mantienen el registro de evidencia y sus metadatos. Al eliminar una evidencia antes de ese plazo, también se borran sus archivos asociados.

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
| `EVIDENCE_MAX_FILE_SIZE`, `EVIDENCE_MAX_IMAGE_PIXELS` | Límites por archivo de tamaño (bytes) y resolución (píxeles). |
| `EVIDENCE_ALLOWED_FORMATS` | Formatos de imagen permitidos, separados por comas (`JPEG,PNG,WEBP` por defecto). |
| `EVIDENCE_RETENTION_DAYS` | Días de conservación del archivo antes de purgarlo; el registro permanece. |
| `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` | Orígenes explícitos permitidos, separados por comas. |
| `EMAIL_BACKEND` | Consola en desarrollo (`DEBUG=True`) y SMTP en producción por defecto; se puede sobrescribir. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL` | Configuración SMTP para enviar correo fuera del entorno local. |
| `DEFAULT_FROM_EMAIL` | Dirección remitente de correos del SGR. |

## Funcionalidad principal

- Actividad diaria con ubicación GPS, dirección y compromiso vinculado; al cambiar
  el funcionario, los ítems se recargan según su cargo y el período activo. Los
  tipos de actividad se administran por área y se filtran para el funcionario.
  El teléfono chileno de contacto es obligatorio al registrar una actividad y
  queda guardado como parte de su historial. La API y el registro web validan
  fechas, vigencia y cargo del ítem; los errores del formulario indican el campo
  que debe corregirse.
- Configuración de ítems y ponderaciones por conjunto cargo/período: el guardado
  es atómico y solo acepta que los ítems vigentes sumen exactamente 100%.
- Metas por ítem y umbrales por período: los cambios incrementan la versión y
  guardan una copia inmutable del conjunto completo de parámetros e ítems.
- Evidencias fotográficas asociadas a actividades, con validación por rol.
- Casos sociales con acceso limitado por rol y delegación, con tipos de atención
  y subatenciones administrables por área.
- Semáforo trimestral con umbrales configurables en la base de datos.
- API REST autenticada bajo `/api/`.

Los mantenedores se gestionan desde las interfaces HTML propias. La API REST permite
consultar delegaciones, cargos, usuarios, períodos, catálogo de servicios e ítems,
pero sus endpoints de mantenimiento son de solo lectura; las operaciones
transaccionales de la API conservan sus permisos por rol. La acción Eliminar de los
mantenedores desactiva el registro y conserva sus relaciones e historial.
Los cálculos y la evaluación del período usan el mismo par de umbrales del semáforo:
verde y ámbar respecto de la meta esperada. Las columnas antiguas se mantienen
sincronizadas por compatibilidad. Cada período conserva una versión inicial de su
configuración actual y una nueva copia inmutable cada vez que se cambia un parámetro,
meta, ponderación o estado de ítem desde los mantenedores.
Los catálogos de tipos de actividad y tipos de atención se asocian a un área; cada
subatención pertenece a un tipo de atención y hereda su área. Las actividades y
atenciones guardan copias del nombre y área seleccionados, por lo que cambiar o
desactivar un catálogo no modifica el historial.
Las ponderaciones de ítems se mantienen desde «Configurar ítems y ponderaciones»,
que valida el conjunto completo antes de guardarlo; los cambios individuales de
estado o ponderación no se permiten para evitar dejar una suma incompleta.
La API no ofrece la interfaz navegable de Django REST Framework: las plantillas
propias consumen únicamente las respuestas JSON necesarias para sus flujos, como
la sincronización de actividades sin conexión.

Los supervisores pueden consultar los mantenedores, pero solo pueden crear,
modificar y desactivar solicitantes y servicios del catálogo. La configuración de
usuarios/funcionarios, roles, áreas, delegaciones, cargos, períodos, ítems y
catálogos de actividad/atención queda reservada al Administrador. El perfil Delegado
no obtiene acceso al menú de mantenedores.
