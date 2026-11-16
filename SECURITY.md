# Seguridad - Matriz SGR

Controles de seguridad del sistema y su relación con **OWASP Top 10 (2021)**.

## Controles por categoría

| OWASP | Control | Ubicación |
|---|---|---|
| A01 Control de acceso | Login obligatorio en todas las vistas; permisos por rol, delegación y objeto ([matriz verificada por pruebas](docs/MATRIZ_PERMISOS.md)); **nadie valida evidencias propias**; evidencias solo con sesión | `sgr_backend/settings.py`, `sgr_app/permissions.py` |
| A02 Criptografía | Contraseñas con PBKDF2; códigos de recuperación con hash; **SHA-256 del contenido real** de cada fotografía (`Evidencia.verificar_integridad`); HTTPS y cookies `Secure` en producción; URLs firmadas y cifrado SSE-S3; respaldos cifrados en S3 y disco EBS cifrado | `sgr_app/services/evidencias.py`, `sgr_app/models.py` |
| A03 Inyección / XSS | ORM sin SQL directo; autoescape de plantillas; CSRF en todos los formularios; validación de RUN y teléfono | `sgr_app/validators.py` |
| A04 Diseño seguro | Transacciones atómicas con bloqueo de filas; **control de concurrencia** (huella en formularios, ETag/If-Match en la API) contra sobrescrituras silenciosas; bloqueo de periodos; retención y purga programada de evidencias | `sgr_app/concurrencia.py`, `sgr_app/mantenedores.py` |
| A05 Configuración | `SECRET_KEY` obligatoria; `DEBUG` apagado por defecto; CORS cerrado; `X-Frame-Options: DENY`; `nosniff`; `Referrer-Policy`; **Content-Security-Policy** | `sgr_backend/settings.py`, `sgr_app/middleware.py` |
| A06 Componentes vulnerables | Versiones exactas en `requirements.txt`; `pip-audit` en CI | `backend/requirements*.txt`, `.github/workflows/ci.yml` |
| A07 Autenticación | **2FA TOTP activable por cada usuario y obligatoria por rol** (`TWO_FACTOR_REQUIRED_ROLES`); validadores de contraseña; **bloqueo temporal tras 5 intentos fallidos** (usuario+IP) y 30 por IP; **bloqueo de 2FA tras 5 códigos erróneos**; sin enumeración de usuarios; solo autenticación por sesión en la API; sesiones de 8 h | `sgr_app/services/seguridad_acceso.py` |
| A08 Integridad | Verificación real de imágenes (Pillow), formatos permitidos, límites de tamaño y resolución; **recodificación que elimina EXIF/GPS** y contenido oculto | `sgr_app/services/evidencias.py` |
| A09 Registro y monitoreo | Auditoría de negocio con **IP real del cliente** y **triggers en la base que impiden modificar o borrar** registros; logger `sgr.security`; monitor de disponibilidad cada minuto; alertas por correo (caídas, errores 500, disco, memoria, respaldos y purga fallidos) | `sgr_app/db_auditoria.py`, `sgr_app/services/operacion.py`, `deploy/systemd/` |
| A10 SSRF | Las únicas peticiones salientes van a una URL fija (Nominatim) con la consulta escapada | `sgr_app/web_views.py` |

## Variables de seguridad

Ver `backend/.env.example` y `AWS_PHPMYADMIN_DEPLOYMENT.md`. En el despliegue en EC2 con Nginx:

- `TRUSTED_PROXY_COUNT=1` (o `2` si se agrega un ALB delante): sin esto, el bloqueo por IP vería a todos como `127.0.0.1`.
- HTTPS con certificado autofirmado en Nginx (no hay dominio); nunca HTTP plano.
- phpMyAdmin solo en `127.0.0.1:8080`, accesible por túnel SSH; MariaDB, Gunicorn y phpMyAdmin sin puertos públicos.
- `CSP_REPORT_ONLY=True` al desplegar; revisar la consola del navegador y pasar a `False` cuando no aparezcan violaciones.

## Verificación automática (CI)

Cada push y PR ejecuta: `pip-audit`, `bandit`, `makemigrations --check`, `check --deploy` y las pruebas con cobertura mínima del 70 %.

Comandos locales equivalentes (desde `backend/`):

```powershell
pip install -r requirements-dev.txt
pip-audit -r requirements.txt
bandit -q -r sgr_app sgr_backend -x "*/migrations/*,*/test*.py,*/tests.py" -ll
coverage run --source=sgr_app manage.py test sgr_app; coverage report
```

## Respaldo y recuperación

Respaldo diario automático (RPO 24 h) con SHA-256, copia local de 7 días y copia cifrada en S3 de 35 días; restauración verificada con `restaurar_base_datos` (ver `AWS_PHPMYADMIN_DEPLOYMENT.md`, sección 10.1).

## Pendiente

- Retirar el JavaScript y los estilos en línea de las plantillas para poder quitar `'unsafe-inline'` de la CSP.
- Evaluar HSTS con subdominios y *preload* cuando el dominio municipal lo permita.
- Antivirus (ClamAV) para evidencias: requiere ~1 GB de RAM adicional; hoy se mitiga con validación y recodificación de imágenes.
- Integración con el directorio institucional (LDAP/Active Directory): requiere acceso a la infraestructura municipal.
- Cifrar `totp_secret` en la base de datos y evitar la reutilización de un mismo código TOTP dentro de su ventana de 30 s.
