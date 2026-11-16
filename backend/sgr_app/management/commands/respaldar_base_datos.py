"""Respaldo automatizado de la base de datos (RNF-010 / RNF-006).

- MariaDB/MySQL: `mariadb-dump --single-transaction` (copia consistente sin
  detener el sistema). SQLite: API de respaldo en caliente de sqlite3.
- Comprime con gzip, calcula SHA-256 y guarda una copia local con permisos 600.
- Si hay bucket configurado, sube el archivo a S3 con cifrado del lado del
  servidor (SSE-S3) bajo el prefijo de respaldos.
- Elimina copias locales más antiguas que BACKUP_LOCAL_DAYS.
- Registra el resultado en EjecucionTarea y alerta si falla.
"""

import gzip
import hashlib
import os
import shutil
import sqlite3
# Se ejecuta un binario fijo (mariadb-dump/mariadb) sin shell.
import subprocess  # nosec B404
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from sgr_app.services.operacion import registrar_tarea

PREFIJO_ARCHIVO = 'sgr-respaldo-'


def _sha256(ruta):
    digest = hashlib.sha256()
    with open(ruta, 'rb') as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b''):
            digest.update(bloque)
    return digest.hexdigest()


def _binario_dump():
    for nombre in ('mariadb-dump', 'mysqldump'):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    raise CommandError('No se encontró mariadb-dump ni mysqldump en el servidor.')


class Command(BaseCommand):
    help = 'Genera un respaldo comprimido de la base de datos y lo sube a S3 cifrado.'

    def add_arguments(self, parser):
        parser.add_argument('--sin-s3', action='store_true', help='Solo copia local.')

    def _volcar_mysql(self, destino_gz):
        db = settings.DATABASES['default']
        comando = [
            _binario_dump(),
            '--single-transaction', '--routines', '--triggers', '--hex-blob',
            '--default-character-set=utf8mb4',
            f'--host={db.get("HOST") or "localhost"}',
            f'--port={db.get("PORT") or "3306"}',
            f'--user={db["USER"]}',
            db['NAME'],
        ]
        # La contraseña va por variable de entorno, no en la línea de comandos
        # (no queda visible en `ps`).
        entorno = {**os.environ, 'MYSQL_PWD': db.get('PASSWORD', '')}
        with gzip.open(destino_gz, 'wb') as salida:
            proceso = subprocess.Popen(  # nosec B603
                comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=entorno,
            )
            shutil.copyfileobj(proceso.stdout, salida)
            _, errores = proceso.communicate()
        if proceso.returncode != 0:
            raise CommandError(f'mariadb-dump terminó con código {proceso.returncode}: '
                               f'{errores.decode("utf-8", "replace")[:500]}')

    def _volcar_sqlite(self, destino_gz):
        origen = sqlite3.connect(str(settings.DATABASES['default']['NAME']))
        with tempfile.TemporaryDirectory() as tmp:
            copia = Path(tmp) / 'copia.sqlite3'
            destino = sqlite3.connect(str(copia))
            try:
                origen.backup(destino)
            finally:
                destino.close()
                origen.close()
            with open(copia, 'rb') as entrada, gzip.open(destino_gz, 'wb') as salida:
                shutil.copyfileobj(entrada, salida)

    def _subir_s3(self, ruta, sha256):
        from sgr_app.services.aws_service import get_s3_client

        bucket = settings.BACKUP_S3_BUCKET
        clave = f'{settings.BACKUP_S3_PREFIX.rstrip("/")}/{ruta.name}'
        get_s3_client().upload_file(
            str(ruta), bucket, clave,
            ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': sha256}},
        )
        return f's3://{bucket}/{clave}'

    def _limpiar_locales(self, directorio):
        limite = timezone.now() - timedelta(days=settings.BACKUP_LOCAL_DAYS)
        for archivo in directorio.glob(f'{PREFIJO_ARCHIVO}*'):
            modificado = datetime.fromtimestamp(archivo.stat().st_mtime, tz=dt_timezone.utc)
            if modificado < limite:
                archivo.unlink(missing_ok=True)

    def handle(self, *args, **options):
        with registrar_tarea('respaldar_base_datos') as resultado:
            inicio = timezone.now()
            directorio = Path(settings.BACKUP_DIR)
            directorio.mkdir(parents=True, exist_ok=True)
            os.chmod(directorio, 0o700)

            motor = connection.vendor
            extension = 'sql.gz' if motor == 'mysql' else 'sqlite3.gz'
            ruta = directorio / f'{PREFIJO_ARCHIVO}{inicio:%Y%m%d-%H%M%S}.{extension}'
            if motor == 'mysql':
                self._volcar_mysql(ruta)
            elif motor == 'sqlite':
                self._volcar_sqlite(ruta)
            else:
                raise CommandError(f'Motor de base de datos no soportado: {motor}')
            os.chmod(ruta, 0o600)

            tamano = ruta.stat().st_size
            if tamano == 0:
                raise CommandError('El respaldo quedó vacío.')
            sha256 = _sha256(ruta)
            ruta.with_name(ruta.name + '.sha256').write_text(f'{sha256}  {ruta.name}\n', encoding='utf-8')

            destino_remoto = 'sin copia remota'
            if settings.BACKUP_S3_BUCKET and not options['sin_s3']:
                destino_remoto = self._subir_s3(ruta, sha256)

            self._limpiar_locales(directorio)
            segundos = (timezone.now() - inicio).total_seconds()
            resultado['detalle'] = (
                f'{ruta.name} ({tamano / 1024:.0f} KiB, sha256={sha256[:16]}…) -> {destino_remoto} '
                f'en {segundos:.1f} s'
            )
            self.stdout.write(self.style.SUCCESS(f'Respaldo listo: {resultado["detalle"]}'))
