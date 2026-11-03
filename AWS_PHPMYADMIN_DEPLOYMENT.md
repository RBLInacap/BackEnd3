# Despliegue del SGR en Amazon Linux con MariaDB

Esta guía despliega el proyecto en una instancia **Amazon Linux 2023** con MariaDB local, Django servido por Gunicorn y Nginx detrás de un **Application Load Balancer (ALB)**. El ALB administra HTTPS con un certificado de AWS Certificate Manager (ACM). No uses `runserver` en EC2 ni publiques MariaDB, Gunicorn o phpMyAdmin en Internet. El archivo `.env` no se obtiene de GitHub ni es necesario: la configuración de producción vive en `/etc/sgr.env`.

> Para sistemas con datos importantes o alta disponibilidad, considera Amazon RDS para MariaDB en lugar de guardar la base en el disco de la instancia. Esta receta usa MariaDB en EC2 porque ese es el destino elegido.

## 1. Red y dominio

Prepara estos recursos en la misma región:

- Un dominio y un certificado validado en AWS Certificate Manager (ACM) para ese dominio.
- Un Application Load Balancer público con listener HTTPS (443) usando el certificado ACM y reenvío a un target group HTTP (80). Configura el listener HTTP (80) para redirigir a HTTPS.
- Un target group hacia la instancia por HTTP en puerto 80. Health check: protocolo HTTP, ruta `/health/`, código exitoso `200`.
- Un registro DNS para que `sgr.tudominio.cl` apunte al **nombre DNS del ALB** (por ejemplo, con un alias de Route 53). No apuntes el dominio a la IPv4 pública de la instancia.
- Un bucket S3 dedicado a las evidencias, con **Block Public Access** habilitado y cifrado habilitado.
- Un IAM instance profile para EC2 con permisos mínimos sobre el bucket S3. No uses claves AWS permanentes.

La IPv4 pública que AWS asigna automáticamente a una instancia puede cambiar al detenerla e iniciarla de nuevo. En esta arquitectura, los usuarios no usan la IP de EC2: acceden al dominio estable del ALB. Registra la EC2 en el target group por **ID de instancia**; el ALB enruta al destino aunque cambie la IP privada de esa instancia. No configures un registro DNS hacia una IP de EC2 que no sea Elastic IP.

En los Security Groups:

- ALB: entrada TCP 80 y 443 desde los clientes.
- EC2: entrada TCP 80 **solo desde el Security Group del ALB**; SSH TCP 22 solo desde tu IP administrativa o usa AWS Systems Manager.
- No abras 3306 (MariaDB), 8000 (Gunicorn), ni un puerto web de phpMyAdmin.

## 2. Instalar paquetes del sistema

Conéctate por SSH y actualiza Amazon Linux 2023:

```bash
sudo dnf update -y
sudo dnf install -y git nginx mariadb105-server mariadb105-devel \
  python3.12 python3.12-devel python3.12-pip gcc pkgconf-pkg-config
sudo systemctl enable --now mariadb
sudo systemctl enable --now nginx
```

Si un nombre de paquete no está disponible en la versión concreta de Amazon Linux, consulta `dnf search mariadb` y `dnf search python3.12` e instala los paquetes equivalentes de esa distribución.

## 3. Crear automáticamente MariaDB, la base y el usuario

```bash
DB_PASSWORD="$(openssl rand -hex 32)"
sudo mariadb <<SQL
CREATE DATABASE IF NOT EXISTS matriz_sgr_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'sgr_user'@'localhost'
  IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER 'sgr_user'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON matriz_sgr_db.* TO 'sgr_user'@'localhost';
FLUSH PRIVILEGES;
SQL
```

Esto crea la base vacía y el usuario dedicado sin entrar al prompt interactivo. La contraseña aleatoria se conserva en `DB_PASSWORD` para escribirla después en `/etc/sgr.env`; no cierres esta sesión antes de crear ese archivo. Si repites este paso, vuelve a generar `/etc/sgr.env` con el mismo valor actual, porque el comando rota la contraseña del usuario. No configures Django como `root`; tampoco habilites conexiones remotas a MariaDB si Django y la base están en la misma instancia.

## 4. Descargar el código e instalar Django

Clona el repositorio en `/srv/sgr/app` como `ec2-user`. Para un repositorio público:

```bash
sudo useradd --system --home-dir /srv/sgr --shell /sbin/nologin sgr
sudo mkdir -p /srv/sgr
sudo chown ec2-user:sgr /srv/sgr
git clone https://github.com/RBLInacap/BackEnd3.git /srv/sgr/app
sudo chown -R ec2-user:sgr /srv/sgr/app
sudo chmod -R u+rwX,go+rX /srv/sgr/app
```

Si es privado, crea una clave SSH de solo lectura y registra su clave pública como *Deploy key* en GitHub. No pongas un token en la URL:

```bash
ssh-keygen -t ed25519 -C "ec2-sgr-deploy" -f ~/.ssh/github_sgr
cat ~/.ssh/github_sgr.pub
GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github_sgr -o IdentitiesOnly=yes" \
  git clone git@github.com:RBLInacap/BackEnd3.git /srv/sgr/app
sudo chown -R ec2-user:sgr /srv/sgr/app
sudo chmod -R u+rwX,go+rX /srv/sgr/app
```

Instala el proyecto desde sus requisitos en un entorno virtual aislado:

```bash
sudo install -d -o sgr -g sgr /srv/sgr/venv
sudo -u sgr python3.12 -m venv /srv/sgr/venv
sudo -u sgr /srv/sgr/venv/bin/python -m pip install --upgrade pip
sudo -u sgr /srv/sgr/venv/bin/pip install -r /srv/sgr/app/backend/requirements.txt
sudo install -d -o sgr -g nginx -m 2750 /srv/sgr/app/backend/staticfiles
```

El proyecto fija Django **5.2.10** en sus requisitos. Instala desde ese archivo; no instales `django<5.0` ni agregues el parche local de `login_not_required`, que solo disimula la incompatibilidad con Django 4.2.

## 5. Crear la configuración de producción fuera de GitHub

Genera una `SECRET_KEY` aleatoria en la instancia:

```bash
SECRET_KEY="$(/srv/sgr/venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))')"
```

El nombre de bucket y la región deben corresponder a los recursos que creaste. Crea el archivo directamente en EC2; no es necesario que GitHub contenga `.env` ni `.env.example`. Primero se crea con permisos restringidos y después se escribe:

```bash
sudo install -o root -g sgr -m 640 /dev/null /etc/sgr.env
sudo tee /etc/sgr.env >/dev/null <<EOF
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ALLOWED_HOSTS=sgr.tudominio.cl

USE_MYSQL=True
DB_NAME=matriz_sgr_db
DB_USER=sgr_user
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=localhost
DB_PORT=3306

USE_S3=True
AWS_STORAGE_BUCKET_NAME=NOMBRE-DEL-BUCKET
AWS_S3_REGION_NAME=us-east-1
AWS_S3_CUSTOM_DOMAIN=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False
CSRF_TRUSTED_ORIGINS=https://sgr.tudominio.cl
TRUST_PROXY_SSL_HEADER=True

CORS_ALLOWED_ORIGINS=
EMAIL_BACKEND=
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=
EOF

sudo chown root:sgr /etc/sgr.env
sudo chmod 640 /etc/sgr.env
unset SECRET_KEY DB_PASSWORD
```

Reemplaza `sgr.tudominio.cl`, `NOMBRE-DEL-BUCKET` y la región por sus valores reales antes de guardar. Asigna a la instancia el **IAM instance profile** con permisos mínimos de lectura, escritura y borrado en el bucket. Deja vacías las variables de claves AWS para usar las credenciales temporales del rol; la configuración las convierte en `None` para permitir la cadena estándar de credenciales de boto3. El bucket debe bloquear el acceso público. No guardes `.env`, contraseñas, claves AWS o secretos en Git.

Las evidencias contienen información municipal: no publiques `MEDIA_ROOT` como directorio estático. El almacenamiento privado S3 ya está soportado por el proyecto y produce URLs firmadas. Comprueba que los objetos no sean accesibles sin firma. `SECURE_HSTS_SECONDS=0` es deliberado al inicio; considera activarlo solo después de comprobar HTTPS de extremo a extremo.

## 6. Migrar, preparar estáticos y crear el administrador

El repositorio incluye las migraciones versionadas de `sgr_app`, desde `0001_initial` hasta `0021_fechas_etapas_caso_social.py`. `manage.py migrate` crea automáticamente las tablas, índices, restricciones y demás estructura de Django en la base vacía que se creó en el paso anterior. No ejecutes SQL manual para crear las tablas ni copies `db.sqlite3` para preparar el esquema.

```bash
sudo -u sgr bash -c 'set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py check --deploy'
sudo -u sgr bash -c 'set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py migrate'
sudo -u sgr bash -c 'set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py collectstatic --noinput'
sudo -u sgr bash -c 'set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py createsuperuser'
```

No ejecutes `poblar_sgr` en una base con datos reales: crea datos sintéticos de demostración. Configura también una política de copias de seguridad y prueba la restauración de MariaDB y del bucket.

### Si también necesitas transferir los datos actuales

Las migraciones copian el **esquema**, no los registros de SQLite/MySQL que tienes hoy. Para trasladar los registros desde el SQLite local del proyecto, primero despliega el código y ejecuta `migrate` en EC2. Luego, desde el computador que tenga `backend/db.sqlite3`, usa el entorno virtual del proyecto y exporta los registros. Se excluyen tablas que Django vuelve a crear durante las migraciones:

```powershell
cd backend
$env:DEBUG = "True"
$env:USE_MYSQL = "False"
$env:SECRET_KEY = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude sessions.session --indent 2 --output C:\sgr-data.json
```

Transfiere ese archivo por un canal administrativo cifrado (por ejemplo, `scp` sobre SSH, SSM/S3 privado temporal) y no lo subas a GitHub: contiene información personal, usuarios y hashes de contraseñas. Para una EC2 con dirección accesible por SSH:

```powershell
scp C:\sgr-data.json ec2-user@<ELASTIC_IP_O_DNS_ADMINISTRATIVO>:/tmp/sgr-data.json
```

En Windows, ejecuta `scp` desde PowerShell y sustituye el destino por una Elastic IP o DNS estable. En EC2, importa los registros después de `migrate`:

```bash
sudo chown sgr:sgr /tmp/sgr-data.json
sudo chmod 600 /tmp/sgr-data.json
sudo -u sgr bash -c 'set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py loaddata /tmp/sgr-data.json'
sudo rm -f /tmp/sgr-data.json
```

`dumpdata`/`loaddata` transfiere registros Django, pero **no copia los archivos** almacenados bajo `backend/media/`, ni datos externos que no estén en la base. Haz copia de seguridad antes de importar y verifica conteos y acceso. Para archivos de evidencia, desde la raíz del proyecto en PowerShell, usa AWS CLI con una identidad autorizada para escribir en el bucket. El comando conserva las rutas relativas (`evidencias/...`):

```powershell
$S3_BUCKET = "nombre-real-del-bucket"
aws sts get-caller-identity
aws s3 sync .\backend\media\ "s3://$S3_BUCKET/" --profile nombre-de-tu-perfil
```

No habilites acceso público al bucket. No publiques los archivos ni el fixture. Si la fuente ya es una base MySQL/MariaDB, usa una copia lógica con `mysqldump` y revisa compatibilidad/collation antes de importarla; no mezcles ese procedimiento con el fixture SQLite.

## 7. Ejecutar Gunicorn con systemd

Crea `/etc/systemd/system/sgr.service`:

```ini
[Unit]
Description=SGR Django application
After=network.target mariadb.service
Requires=mariadb.service

[Service]
Type=simple
User=sgr
Group=sgr
WorkingDirectory=/srv/sgr/app/backend
EnvironmentFile=/etc/sgr.env
ExecStart=/srv/sgr/venv/bin/gunicorn --workers 2 --timeout 120 --bind 127.0.0.1:8000 sgr_backend.wsgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activa el servicio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sgr
sudo systemctl status sgr
```

Gunicorn escucha solo en `127.0.0.1`; no abras el puerto 8000 en el Security Group.

## 8. Configurar Nginx detrás del ALB

```nginx
server {
    listen 80;
    server_name sgr.tudominio.cl;

    client_max_body_size 10m;

    location = /health/ {
        proxy_pass http://127.0.0.1:8000/login/;
        proxy_set_header Host sgr.tudominio.cl;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /static/ {
        alias /srv/sgr/app/backend/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    }
}
```

Este Nginx escucha HTTP solo en la interfaz EC2, pero el Security Group acepta conexiones al puerto 80 exclusivamente desde el ALB. El ALB termina HTTPS y establece `X-Forwarded-Proto`; Nginx lo reenvía a Django. `TRUST_PROXY_SSL_HEADER=True` solo es seguro bajo esas condiciones. No permitas que clientes de Internet lleguen directamente al puerto 80 de EC2.

Valida y recarga:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

No añadas una ubicación Nginx pública para `/media/`: las evidencias deben permanecer privadas en S3 y servirse con URL firmada. Los estáticos de `/static/` sí pueden servirse directamente por Nginx.

Verifica el health check y el proxy desde la instancia. El health check proxifica a la página pública de login para confirmar que Nginx y Django responden:

```bash
curl -i -H 'Host: sgr.tudominio.cl' http://127.0.0.1/health/
curl -I -H 'Host: sgr.tudominio.cl' \
  -H 'X-Forwarded-Proto: https' http://127.0.0.1/login/
```

El primer comando debe devolver `200 OK`; configúralo como ruta de health check del target group.

## 9. phpMyAdmin sin exponer la administración

phpMyAdmin no es necesario para que Django funcione. Si lo necesitas, evita publicarlo en una URL/puerto abierto. Para administración, prefiere AWS Systems Manager Session Manager; así puedes entrar a la instancia sin exponer SSH ni depender de una IPv4 pública. Si necesitas SSH y un túnel desde un equipo local, asigna una **Elastic IP** a la instancia o usa un nombre DNS administrativo que actualices cuando cambie la IP. Sin una dirección estable, la IPv4 pública que usabas para SSH puede dejar de funcionar al detener/iniciar EC2.

```bash
ssh -L 3307:127.0.0.1:3306 ec2-user@<ELASTIC_IP_O_DNS_ADMINISTRATIVO>
```

Con el túnel activo, configura el cliente local para conectarse a `127.0.0.1:3307` con el usuario `sgr_user`. Si instalas phpMyAdmin en EC2, restringe su acceso a localhost, VPN o IP administrativa y exige TLS; nunca expongas una instancia de phpMyAdmin abierta al público.

## 10. HTTP o HTTPS: qué esperar

`runserver`, Gunicorn y el Nginx de esta guía hablan **HTTP** dentro de la instancia; no negocian TLS. En esta arquitectura, el navegador usa `https://sgr.tudominio.cl`, el ALB termina TLS con el certificado ACM y reenvía HTTP a Nginx, que pasa la petición a Gunicorn. Django reconoce la conexión HTTPS original mediante el encabezado reenviado por el ALB.

Si abres `https://<IP>:8000` directamente, aparecerán errores como `You're accessing the development server over HTTPS, but it only supports HTTP` o `Bad request version`: se está enviando una negociación TLS a un servidor HTTP. No es un error de MariaDB ni de las claves primarias.

La aplicación puede responder HTTP si se desactiva `SECURE_SSL_REDIRECT`, pero el tráfico, las contraseñas y las cookies quedarían expuestos. No navegues por HTTP en producción: usa el listener HTTPS del ALB y su redirección HTTP→HTTPS. HTTP se usa solo en los tramos privados ALB→Nginx→Gunicorn.
