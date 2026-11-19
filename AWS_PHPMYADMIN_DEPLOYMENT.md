# Despliegue del SGR en AWS Academy (EC2 + IP elástica, sin dominio)

Esta guía instala el sistema en **una instancia EC2 Amazon Linux 2023** de AWS Academy (Learner Lab), accesible por su **IP elástica** y sin dominio propio.

```
Navegador ──HTTPS 443──▶ Nginx (certificado autofirmado) ──▶ Gunicorn/Django (127.0.0.1:8000)
                                                                   │
                                                  MariaDB (127.0.0.1:3306)   S3 (evidencias privadas)
Tu PC ──túnel SSH──▶ phpMyAdmin (127.0.0.1:8080, nunca público)
```

| Componente | Función |
|---|---|
| **Django + Gunicorn** | La aplicación SGR: pantallas, login, reglas de negocio, permisos. |
| **MariaDB** | Base de datos del sistema. |
| **Nginx** | Recibe HTTPS, entrega archivos estáticos y reenvía el resto a Django. |
| **S3** | Fotos de evidencia, privadas y servidas con URL firmada. |
| **phpMyAdmin** | Panel para administrar MariaDB; solo accesible por túnel SSH. |

> **Sobre HTTPS sin dominio:** las autoridades certificadoras no emiten certificados para una IP, por lo que se usa un **certificado autofirmado**. El navegador mostrará una advertencia la primera vez ("La conexión no es privada"); se acepta una vez y la comunicación queda **cifrada igual**. No usen HTTP plano: las contraseñas y cookies de sesión viajarían legibles. Si más adelante obtienen un dominio, vean la sección 12.

---

## 1. Preparar recursos en AWS Academy

Inicia el laboratorio (**Start Lab**) y abre la consola de AWS cuando el indicador esté en verde. Trabaja en la región del laboratorio (normalmente `us-east-1`).

### 1.1 Security Group

Crea un Security Group `sgr-web` con estas reglas de entrada:

| Tipo | Puerto | Origen | Motivo |
|---|---|---|---|
| SSH | 22 | **Mi IP** | Administración y túnel de phpMyAdmin |
| HTTP | 80 | 0.0.0.0/0 | Solo redirige a HTTPS |
| HTTPS | 443 | 0.0.0.0/0 | Acceso de usuarios |

**No abras** 3306 (MariaDB), 8000 (Gunicorn) ni 8080 (phpMyAdmin). Si tu IP de casa cambia, actualiza la regla SSH.

### 1.2 Instancia EC2

- AMI: **Amazon Linux 2023**.
- Tipo: **t3.small** o superior (MariaDB, Django y PHP juntos no caben cómodos en 1 GB; si solo puedes usar t3.micro, crea el swap del paso 2).
- Par de claves: **vockey** (descarga `labsuser.pem` desde *AWS Details* del Learner Lab).
- Security Group: `sgr-web`.
- Almacenamiento: 20 GB gp3, con **Encrypted: Yes** (cifrado del disco EBS, protege la base y los respaldos locales; RNF-006).
- *Advanced details* → **IAM instance profile: `LabInstanceProfile`**. Es el rol que AWS Academy permite usar; da a la instancia acceso a S3 sin escribir claves en ningún archivo.

### 1.3 IP elástica (la "IP fija")

La IP pública automática de una EC2 **cambia cada vez que la instancia se detiene**, y el Learner Lab la detiene al terminar cada sesión. Para que la dirección no cambie:

1. EC2 → **Elastic IPs** → *Allocate Elastic IP address*.
2. *Actions* → *Associate Elastic IP address* → elige la instancia.

En esta guía esa dirección se escribe como `IP_ELASTICA` (por ejemplo `54.210.10.20`). Todos los usuarios entrarán a `https://IP_ELASTICA`.

### 1.4 Bucket S3 para evidencias

S3 → *Create bucket*:

- Nombre único, por ejemplo `sgr-evidencias-<iniciales>-2026`.
- Misma región que la instancia.
- **Block all public access: activado** (no lo desactives).
- Cifrado por defecto (SSE-S3): activado.
- **Bucket Versioning: Enable** (protege las evidencias ante borrados o sobrescrituras accidentales).

Después de crearlo, en *Management → Lifecycle rules* crea dos reglas:

| Regla | Alcance | Acción |
|---|---|---|
| `respaldos-35-dias` | Prefijo `respaldos/` | Expirar versiones actuales a los **35 días** y eliminar versiones no actuales a 1 día |
| `borrado-definitivo-30-dias` | Todo el bucket | Eliminar **versiones no actuales** a los **30 días** |

La segunda regla garantiza que una evidencia purgada o eliminada desaparezca de forma definitiva (incluidas sus versiones anteriores) a más tardar 30 días después (RNF-017).

## 2. Conectarse e instalar paquetes

Desde PowerShell, en la carpeta donde está `labsuser.pem`:

```powershell
ssh -i labsuser.pem ec2-user@IP_ELASTICA
```

En la instancia:

```bash
sudo dnf update -y
sudo dnf install -y git nginx mariadb105-server mariadb105-devel \
  python3.12 python3.12-devel python3.12-pip gcc pkgconf-pkg-config openssl
sudo systemctl enable --now mariadb
sudo systemctl enable --now nginx
```

Si un nombre de paquete no existe, búscalo con `dnf search mariadb` o `dnf search python3.12`.

Solo si la instancia tiene 1 GB de RAM (t3.micro), agrega 2 GB de swap:

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. Crear la base de datos y su usuario

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

La contraseña aleatoria queda en la variable `DB_PASSWORD` para el paso 5: **no cierres la sesión SSH antes de terminarlo**. Si repites este paso, la contraseña cambia y debes volver a generar `/etc/sgr.env`. Django nunca usa `root`, y MariaDB solo escucha dentro de la instancia.

## 4. Descargar el código e instalar dependencias

```bash
sudo useradd --system --home-dir /srv/sgr --shell /sbin/nologin sgr
sudo mkdir -p /srv/sgr
sudo chown ec2-user:sgr /srv/sgr
git clone https://github.com/RBLInacap/BackEnd3.git /srv/sgr/app
sudo chown -R ec2-user:sgr /srv/sgr/app
sudo chmod -R u+rwX,go+rX /srv/sgr/app
```

Si el repositorio es **privado**, crea una clave de solo lectura y regístrala en GitHub (*Settings → Deploy keys*). No pongas tokens en la URL:

```bash
ssh-keygen -t ed25519 -C "ec2-sgr-deploy" -f ~/.ssh/github_sgr
cat ~/.ssh/github_sgr.pub     # pégala como Deploy key en GitHub
GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github_sgr -o IdentitiesOnly=yes" \
  git clone git@github.com:RBLInacap/BackEnd3.git /srv/sgr/app
```

Entorno virtual y dependencias (versiones fijas, Django 5.2.19):

```bash
sudo install -d -o sgr -g sgr /srv/sgr/venv
sudo -u sgr python3.12 -m venv /srv/sgr/venv
sudo -u sgr /srv/sgr/venv/bin/python -m pip install --upgrade pip
sudo -u sgr /srv/sgr/venv/bin/pip install -r /srv/sgr/app/backend/requirements.txt
sudo install -d -o sgr -g nginx -m 2750 /srv/sgr/app/backend/staticfiles
```

## 5. Configuración de producción (fuera de GitHub)

La configuración vive en `/etc/sgr.env`, legible solo por root y el usuario `sgr`. **Reemplaza `IP_ELASTICA` y `NOMBRE-DEL-BUCKET`** antes de ejecutar:

```bash
IP_ELASTICA=54.210.10.20            # tu IP elástica real
BUCKET=NOMBRE-DEL-BUCKET            # tu bucket real
SECRET_KEY="$(/srv/sgr/venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))')"

sudo install -o root -g sgr -m 640 /dev/null /etc/sgr.env
sudo tee /etc/sgr.env >/dev/null <<EOF
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ALLOWED_HOSTS=${IP_ELASTICA}
CSRF_TRUSTED_ORIGINS=https://${IP_ELASTICA}

USE_MYSQL=True
DB_NAME=matriz_sgr_db
DB_USER=sgr_user
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=localhost
DB_PORT=3306

# Credenciales vacías: se usan las temporales de LabInstanceProfile.
USE_S3=True
AWS_STORAGE_BUCKET_NAME=${BUCKET}
AWS_S3_REGION_NAME=us-east-1
AWS_S3_CUSTOM_DOMAIN=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# HTTPS lo termina Nginx en esta misma instancia.
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
TRUST_PROXY_SSL_HEADER=True
# HSTS no aplica a direcciones IP; se deja desactivado.
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False

# Un solo proxy (Nginx) delante de Django: permite ver la IP real del cliente.
TRUSTED_PROXY_COUNT=1
# Administradores y supervisores deben activar verificación en dos pasos (RNF-004).
TWO_FACTOR_REQUIRED_ROLES=ADMINISTRADOR,COORDINADOR,DELEGADO
LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_MAX_FAILED_ATTEMPTS_PER_IP=30
TWO_FACTOR_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCKOUT_MINUTES=15
SESSION_COOKIE_AGE=28800
SESSION_EXPIRE_AT_BROWSER_CLOSE=True
# Cambia a False tras revisar la consola del navegador sin violaciones CSP.
CSP_REPORT_ONLY=True
LOG_LEVEL=INFO
LOG_FILE=

# Alertas por correo (caídas, errores 500, disco, respaldos y purga fallidos).
ALERTAS_EMAIL=correo1@ejemplo.cl,correo2@ejemplo.cl

# Monitor de disponibilidad: recorre Nginx -> Gunicorn -> Django -> BD -> S3
# verificando el certificado autofirmado.
MONITOR_URL=https://${IP_ELASTICA}/health/?profundo=1
MONITOR_CA_FILE=/etc/pki/nginx/sgr.crt
MONITOR_STATE_FILE=/var/lib/sgr/monitor.json
MONITOR_DISK_ALERT_PCT=85
# Jornada operativa para medir disponibilidad (0 = lunes) y meta mensual.
JORNADA_DIAS=0,1,2,3,4
JORNADA_HORAS=08:00-18:00
DISPONIBILIDAD_META_PCT=99.5

# Respaldos: copia local 7 días + copia cifrada en S3 (prefijo respaldos/).
BACKUP_DIR=/var/backups/sgr
BACKUP_LOCAL_DAYS=7
BACKUP_S3_PREFIX=respaldos/

CORS_ALLOWED_ORIGINS=
# Correo para recuperación de contraseña (Gmail: usa una "contraseña de aplicación").
EMAIL_BACKEND=
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=
EOF

unset SECRET_KEY DB_PASSWORD
```

Notas:

- Para editar después: `sudo nano /etc/sgr.env` y luego `sudo systemctl restart sgr`.
- Sin `EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`, la recuperación de contraseña por correo no funcionará. Para Gmail, crea una *contraseña de aplicación* (requiere verificación en dos pasos en esa cuenta) y escríbela **solo** en este archivo.
- `TRUSTED_PROXY_COUNT=1` y `TRUST_PROXY_SSL_HEADER=True` son seguros porque Nginx **reemplaza** esas cabeceras (paso 8) y Gunicorn solo escucha en `127.0.0.1`.
- Nunca subas `/etc/sgr.env`, `.env`, contraseñas ni `labsuser.pem` a GitHub.

## 6. Crear las tablas, estáticos y el administrador

```bash
run_django() { sudo -u sgr bash -c "set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py $(printf '%q ' "$@")"; }

run_django check --deploy
run_django migrate
run_django collectstatic --noinput
run_django createsuperuser
run_django test_aws          # comprueba el acceso al bucket S3
```

`migrate` crea todas las tablas (migraciones `0001` a `0030`) en la base vacía. No ejecutes SQL manual ni copies `db.sqlite3`. **No ejecutes `poblar_sgr`** en una base con datos reales: genera datos de demostración.

### Si necesitas trasladar datos existentes

Las migraciones crean la estructura, no los registros. Desde el computador que tiene `backend/db.sqlite3`:

```powershell
cd backend
$env:DEBUG = "True"; $env:USE_MYSQL = "False"
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(50))"
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude sessions.session --indent 2 --output C:\sgr-data.json
scp -i labsuser.pem C:\sgr-data.json ec2-user@IP_ELASTICA:/tmp/sgr-data.json
```

En la instancia (después de `migrate`):

```bash
sudo chown sgr:sgr /tmp/sgr-data.json && sudo chmod 600 /tmp/sgr-data.json
run_django loaddata /tmp/sgr-data.json
sudo rm -f /tmp/sgr-data.json
```

El archivo contiene datos personales y hashes de contraseñas: no lo subas a GitHub y bórralo al terminar. Las fotos de `backend/media/` no viajan en ese archivo; súbelas al bucket con AWS CLI (`aws s3 sync .\backend\media\ s3://NOMBRE-DEL-BUCKET/`), usando las credenciales temporales de *AWS Details* del Learner Lab.

## 7. Ejecutar Django con Gunicorn (systemd)

```bash
sudo tee /etc/systemd/system/sgr.service >/dev/null <<'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sgr
sudo systemctl status sgr --no-pager
```

### 7.1 Tareas automáticas: monitoreo, respaldo y purga

El repositorio incluye las unidades de systemd en `deploy/systemd/`. Instálalas **después** de configurar Nginx (paso 8), porque el monitor consulta `https://IP_ELASTICA/health/`:

```bash
sudo bash /srv/sgr/app/deploy/instalar_tareas.sh
```

| Temporizador | Frecuencia | Qué hace |
|---|---|---|
| `sgr-monitor.timer` | cada minuto | Consulta `/health/` (BD, S3, disco); registra caídas con inicio, fin y causa; detecta periodos con el servidor apagado; alerta por correo al caer y al recuperarse, por disco/memoria altos y por tareas atrasadas. |
| `sgr-respaldo.timer` | diario 02:00 | Respaldo de MariaDB comprimido, con SHA-256, a `/var/backups/sgr` (7 días) y a S3 cifrado (35 días). **RPO: 24 h.** |
| `sgr-purga.timer` | diario 03:00 | Elimina los archivos de evidencia vencidos (`EVIDENCE_RETENTION_DAYS`). |
| `sgr-coherencia.timer` | diario 04:00 | Revisa la coherencia de los datos (solo lectura) y envía correo si hay hallazgos críticos o advertencias. |

`Persistent=true` hace que, si AWS Academy tenía la instancia apagada a esa hora, el respaldo y la purga se ejecuten al encenderla. Si una tarea falla, systemd envía una alerta por correo (`sgr-alerta@`). Prueba que todo funcione:

```bash
sudo systemctl start sgr-respaldo.service && sudo journalctl -u sgr-respaldo -n 5 --no-pager
sudo systemctl start sgr-monitor.service && sudo journalctl -u sgr-monitor -n 3 --no-pager
curl -sk https://127.0.0.1/health/?profundo=1 -H "Host: ${IP_ELASTICA}"
```

`enable` hace que el sistema arranque solo cada vez que el Learner Lab enciende la instancia.

## 8. HTTPS con Nginx

### 8.1 Certificado autofirmado para la IP

```bash
IP_ELASTICA=54.210.10.20     # tu IP elástica real
sudo mkdir -p /etc/pki/nginx/private
sudo openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout /etc/pki/nginx/private/sgr.key \
  -out /etc/pki/nginx/sgr.crt \
  -subj "/CN=${IP_ELASTICA}" \
  -addext "subjectAltName=IP:${IP_ELASTICA}"
sudo chmod 600 /etc/pki/nginx/private/sgr.key
```

Si algún día cambias de IP elástica, repite este paso y actualiza `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS`.

### 8.2 Sitio Nginx

Crea `/etc/nginx/conf.d/sgr.conf`:

```bash
sudo tee /etc/nginx/conf.d/sgr.conf >/dev/null <<'EOF'
# HTTP solo redirige a HTTPS.
server {
    listen 80 default_server;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2 default_server;
    server_name _;

    ssl_certificate     /etc/pki/nginx/sgr.crt;
    ssl_certificate_key /etc/pki/nginx/private/sgr.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;

    client_max_body_size 10m;
    server_tokens off;

    location /static/ {
        alias /srv/sgr/app/backend/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        # Se REEMPLAZAN (no se reenvían) para que el cliente no pueda falsificarlas.
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF
```

`nginx.conf` de Amazon Linux trae un servidor de ejemplo en el puerto 80. Si `nginx -t` informa `duplicate default server`, quita `default_server` de las líneas `listen` de ese bloque en `/etc/nginx/nginx.conf`.

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -kI https://127.0.0.1/login/ -H "Host: ${IP_ELASTICA}"   # debe responder 200
```

Abre `https://IP_ELASTICA` en el navegador, acepta la advertencia del certificado e inicia sesión con el superusuario. Como el rol administrador exige verificación en dos pasos, el sistema te llevará a activarla: escanea el QR con Google Authenticator o Microsoft Authenticator.

La documentación de la API para integraciones queda en `https://IP_ELASTICA/api/v1/docs/` (requiere sesión).

No crees una ubicación pública para `/media/`: las evidencias están en S3 y se sirven con URL firmada.

## 9. phpMyAdmin (solo por túnel SSH)

phpMyAdmin permite ver y administrar MariaDB desde el navegador. Se instala escuchando **solo en `127.0.0.1:8080`**: no se abre ningún puerto y se entra por un túnel SSH. Un phpMyAdmin público es de lo más atacado en Internet.

### 9.1 Instalar PHP-FPM

```bash
sudo dnf install -y php8.3 php8.3-fpm php8.3-mysqlnd php8.3-mbstring php8.3-xml php8.3-zip
sudo systemctl enable --now php-fpm
```

Si esos nombres no existen, busca la versión disponible con `dnf search php8` e instala los mismos módulos.

### 9.2 Descargar phpMyAdmin y verificar su integridad

Consulta la última versión estable en <https://www.phpmyadmin.net/downloads/> y úsala en `PMA_VERSION`:

```bash
PMA_VERSION=5.2.3
cd /tmp
curl -fLO "https://files.phpmyadmin.net/phpMyAdmin/${PMA_VERSION}/phpMyAdmin-${PMA_VERSION}-all-languages.tar.gz"
curl -fLO "https://files.phpmyadmin.net/phpMyAdmin/${PMA_VERSION}/phpMyAdmin-${PMA_VERSION}-all-languages.tar.gz.sha256"
sha256sum -c "phpMyAdmin-${PMA_VERSION}-all-languages.tar.gz.sha256"   # debe decir: OK
sudo mkdir -p /usr/share/phpmyadmin
sudo tar -xzf "phpMyAdmin-${PMA_VERSION}-all-languages.tar.gz" --strip-components=1 -C /usr/share/phpmyadmin
sudo install -d -o apache -g apache -m 750 /var/lib/phpmyadmin/tmp
rm -f phpMyAdmin-*
cd ~
```

No continúes si `sha256sum` no responde `OK`.

### 9.3 Configurar phpMyAdmin

```bash
PMA_SECRET="$(openssl rand -hex 16)"   # 32 caracteres, lo que exige phpMyAdmin
sudo tee /usr/share/phpmyadmin/config.inc.php >/dev/null <<EOF
<?php
\$cfg['blowfish_secret'] = '${PMA_SECRET}';
\$cfg['TempDir'] = '/var/lib/phpmyadmin/tmp';
\$i = 1;
\$cfg['Servers'][\$i]['auth_type'] = 'cookie';
\$cfg['Servers'][\$i]['host'] = 'localhost';
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;
\$cfg['Servers'][\$i]['AllowRoot'] = false;
\$cfg['LoginCookieValidity'] = 1800;
EOF
sudo chown root:apache /usr/share/phpmyadmin/config.inc.php
sudo chmod 640 /usr/share/phpmyadmin/config.inc.php
sudo rm -rf /usr/share/phpmyadmin/setup
unset PMA_SECRET
```

Se ingresa con el usuario `sgr_user` y su contraseña (`sudo grep DB_PASSWORD /etc/sgr.env`). `root` está bloqueado en phpMyAdmin.

### 9.4 Publicarlo solo dentro de la instancia

```bash
sudo tee /etc/nginx/conf.d/phpmyadmin.conf >/dev/null <<'EOF'
server {
    listen 127.0.0.1:8080;
    server_name localhost;
    root /usr/share/phpmyadmin;
    index index.php;
    server_tokens off;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ \.php$ {
        try_files $uri =404;
        fastcgi_pass unix:/run/php-fpm/www.sock;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }

    location ~ ^/(libraries|templates|vendor|sql|tmp)/ {
        deny all;
    }
}
EOF
sudo nginx -t && sudo systemctl reload nginx
curl -I http://127.0.0.1:8080/      # debe responder 200
```

### 9.5 Entrar desde tu computador

En PowerShell:

```powershell
ssh -i labsuser.pem -L 8080:127.0.0.1:8080 ec2-user@IP_ELASTICA
```

Mientras esa sesión esté abierta, entra a <http://localhost:8080/> en tu navegador. El tráfico viaja cifrado dentro del túnel SSH; al cerrar la sesión, phpMyAdmin deja de ser accesible.

**No** abras el puerto 8080 en el Security Group ni cambies `listen 127.0.0.1:8080`.

## 10. Operación diaria en AWS Academy

En cada sesión SSH nueva, define primero el atajo para comandos de Django:

```bash
run_django() { sudo -u sgr bash -c "set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py $(printf '%q ' "$@")"; }
```

- **Encender:** *Start Lab* enciende la instancia; Django, MariaDB, Nginx y PHP arrancan solos. Espera 1-2 minutos y entra a `https://IP_ELASTICA`.
- **Al terminar la sesión** el laboratorio detiene la instancia; los datos (MariaDB y S3) se conservan. Si el sitio no responde, revisa en EC2 que la instancia esté *running*.
- **Créditos:** la IP elástica y el disco consumen saldo aunque la instancia esté detenida (poco). No crees instancias ni IPs de más y libera las que no uses.
- **Revisar estado y registros:**

```bash
sudo systemctl status sgr nginx mariadb php-fpm --no-pager
sudo journalctl -u sgr -n 100 --no-pager        # errores, inicios de sesión, bloqueos
sudo tail -n 50 /var/log/nginx/error.log
```

- **Disponibilidad mensual** dentro de la jornada operativa, frente a la meta (RNF-001):

```bash
run_django informe_disponibilidad --mes 2026-11
run_django registrar_mantenimiento --inicio "2026-11-20 20:00" --fin "2026-11-20 22:00" --motivo "Actualización"
```

  Las ventanas registradas como mantenimiento no cuentan como caída. Los periodos con la instancia apagada sí cuentan como no disponibilidad: **en AWS Academy el laboratorio apaga la instancia al terminar cada sesión, por lo que la meta de 99,5 % solo es alcanzable en una cuenta AWS normal** con la instancia encendida de forma permanente.

- **Historial de tareas automáticas:** `sudo journalctl -u 'sgr-*' --since today`.
- **Coherencia de los datos** (solo lectura; no modifica nada):

```bash
run_django verificar_coherencia            # informe por severidad: CRITICO, ADVERTENCIA, INFO
run_django verificar_coherencia --json     # para guardar o procesar el resultado
```

  Revisa claves huérfanas en todas las tablas y 35 reglas de negocio (columnas duplicadas que divergen, emails o RUN repetidos, períodos superpuestos, ponderaciones que no suman 100 %, evidencias autovalidadas, compromisos realizados sin fecha, etc.). Cada hallazgo indica los ID afectados y la acción sugerida. Ejecútalo antes de cualquier migración que modifique el esquema.

### 10.1 Respaldo manual y prueba de restauración (RTO)

```bash
run_django respaldar_base_datos           # respaldo inmediato (antes de cambios grandes)
run_django restaurar_base_datos --listar  # respaldos locales y en S3
```

**Prueba de restauración trimestral** (comprueba que los respaldos sirven y mide el RTO; objetivo inicial: 4 horas). Hazla en una **instancia de prueba** creada igual que esta guía (pasos 1-7), nunca sobre producción:

```bash
run_django restaurar_base_datos --s3 sgr-respaldo-AAAAMMDD-HHMMSS.sql.gz --confirmar
sudo systemctl restart sgr
```

El comando verifica el SHA-256 del respaldo antes de restaurar e informa el tiempo empleado. Registra la fecha, el respaldo usado, el tiempo total desde que empezaste (incluida la creación de la instancia) y el resultado en el registro de pruebas del equipo. Las evidencias no requieren restauración: permanecen en S3 con versionado.

Si alguna vez restauras en producción: detén la aplicación (`sudo systemctl stop sgr`), haz primero un respaldo del estado actual y luego restaura.

## 11. Actualizar el sistema con cambios de GitHub

```bash
cd /srv/sgr/app
git pull
sudo -u sgr /srv/sgr/venv/bin/pip install -r backend/requirements.txt
run_django() { sudo -u sgr bash -c "set -a; . /etc/sgr.env; set +a; cd /srv/sgr/app/backend; /srv/sgr/venv/bin/python manage.py $(printf '%q ' "$@")"; }
run_django migrate
run_django collectstatic --noinput
sudo systemctl restart sgr
```

Antes de `migrate`, ejecuta `run_django respaldar_base_datos` (sección 10.1).

## 12. Si más adelante tienen un dominio

Con un dominio propio se puede reemplazar el certificado autofirmado por uno válido y gratuito de **Let's Encrypt** (`certbot`), eliminando la advertencia del navegador, y activar HSTS (`SECURE_HSTS_SECONDS=31536000`). En ese caso cambien `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` al nombre del dominio. Para un despliegue institucional real (Municipalidad), se recomienda además Amazon RDS para la base de datos y un Application Load Balancer con certificado de AWS Certificate Manager.
