# Guía de Despliegue en AWS: Matriz SGR con MySQL y phpMyAdmin

Esta guía está preparada para **Daninson** (DevOps / Infraestructura) para levantar la plataforma **Matriz SGR (Ilustre Municipalidad de La Serena)** en AWS conectada a una base de datos MySQL / MariaDB y con administración visual mediante **phpMyAdmin**.

---

## 1. Variables de Entorno en el Backend (Django)

El archivo `backend/sgr_backend/settings.py` ya se encuentra configurado para detectar automáticamente MySQL si se definen las variables de entorno. De lo contrario, utilizará SQLite para desarrollo local sin romper nada.

Para activar MySQL en AWS (EC2, ECS o Docker), configure las siguientes variables de entorno:

```bash
export USE_MYSQL=True
export DB_NAME=matriz_sgr_db
export DB_USER=sgr_user
export DB_PASSWORD=TuPasswordSegura2026!
export DB_HOST=localhost            # o endpoint de Amazon RDS (ej: db.xxxx.us-east-1.rds.amazonaws.com)
export DB_PORT=3306
```

---

## 2. Opción Rápida con Docker Compose (Recomendada para AWS EC2)

Crea un archivo `docker-compose.yml` en la raíz de la instancia EC2 para levantar Django, MySQL y phpMyAdmin en un solo comando:

```yaml
version: '3.8'

services:
  # Base de Datos MySQL
  db:
    image: mysql:8.0
    restart: always
    environment:
      MYSQL_DATABASE: matriz_sgr_db
      MYSQL_USER: sgr_user
      MYSQL_PASSWORD: sgr_password2026!
      MYSQL_ROOT_PASSWORD: root_password2026!
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql

  # phpMyAdmin para administración visual en AWS
  phpmyadmin:
    image: phpmyadmin/phpmyadmin:latest
    restart: always
    environment:
      PMA_HOST: db
      PMA_PORT: 3306
      PMA_USER: sgr_user
      PMA_PASSWORD: sgr_password2026!
      UPLOAD_LIMIT: 64M
    ports:
      - "8080:80"   # Acceso: http://<IP-PUBLICA-AWS>:8080
    depends_on:
      - db

  # Aplicación Django (Matriz SGR)
  web:
    build:
      context: .
      dockerfile: Dockerfile
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - ./backend:/app
    ports:
      - "8000:8000"  # Acceso: http://<IP-PUBLICA-AWS>:8000
    environment:
      - USE_MYSQL=True
      - DB_NAME=matriz_sgr_db
      - DB_USER=sgr_user
      - DB_PASSWORD=sgr_password2026!
      - DB_HOST=db
      - DB_PORT=3306
    depends_on:
      - db

volumes:
  mysql_data:
```

### Ejecución:
```bash
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py poblar_datos_la_serena
```

---

## 3. Opción Nativa (Ubuntu 22.04 / 24.04 en AWS EC2)

Si se instala directamente sobre la máquina virtual:

### 3.1 Instalar MySQL y phpMyAdmin
```bash
sudo apt update
sudo apt install mysql-server phpmyadmin php-mbstring php-zip php-gd php-json php-curl -y
```

### 3.2 Crear la Base de Datos Municipal
```sql
sudo mysql -u root

CREATE DATABASE matriz_sgr_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sgr_user'@'%' IDENTIFIED BY 'sgr_password2026!';
GRANT ALL PRIVILEGES ON matriz_sgr_db.* TO 'sgr_user'@'%';
FLUSH PRIVILEGES;
EXIT;
```

### 3.3 Configurar Apache para phpMyAdmin
```bash
sudo ln -s /usr/share/phpmyadmin /var/www/html/phpmyadmin
sudo systemctl restart apache2
```
*Acceso a phpMyAdmin:* `http://<IP-PUBLICA-EC2>/phpmyadmin`  
(Usuario: `sgr_user` / Contraseña: `sgr_password2026!`)

### 3.4 Iniciar el Backend Django
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export USE_MYSQL=True
export DB_NAME=matriz_sgr_db
export DB_USER=sgr_user
export DB_PASSWORD=sgr_password2026!
export DB_HOST=127.0.0.1
export DB_PORT=3306

python manage.py migrate
python manage.py poblar_datos_la_serena
python manage.py runserver 0.0.0.0:8000
```

---

## 4. Reglas del Security Group en AWS

Asegurarse de abrir en el **Security Group** de la instancia EC2:
- **Puerto 8000 (TCP):** Aplicación Web Django (Matriz SGR).
- **Puerto 8080 o 80 (TCP):** phpMyAdmin (restringir a la IP del equipo si se prefiere mayor seguridad).
- **Puerto 22 (TCP):** Acceso SSH.
