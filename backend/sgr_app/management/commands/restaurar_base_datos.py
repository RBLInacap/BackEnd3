"""Restauración de un respaldo de la base de datos (RNF-010).

Verifica el SHA-256 del respaldo antes de restaurar y mide el tiempo total
(insumo para comprobar el RTO). Exige --confirmar porque reemplaza los datos.

Uso:
    manage.py restaurar_base_datos --listar
    manage.py restaurar_base_datos --s3 sgr-respaldo-20261116-020000.sql.gz --confirmar
    manage.py restaurar_base_datos --archivo /var/backups/sgr/sgr-respaldo-....sql.gz --confirmar
"""

import gzip
import hashlib
import os
import shutil
# Se ejecuta un binario fijo (mariadb-dump/mariadb) sin shell.
import subprocess  # nosec B404
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _sha256(ruta):
    digest = hashlib.sha256()
    with open(ruta, 'rb') as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b''):
            digest.update(bloque)
    return digest.hexdigest()


class Command(BaseCommand):
    help = 'Restaura la base de datos desde un respaldo local o de S3.'

    def add_arguments(self, parser):
        parser.add_argument('--archivo', help='Ruta local del respaldo .gz')
        parser.add_argument('--s3', help='Nombre del respaldo dentro del prefijo de S3')
        parser.add_argument('--listar', action='store_true', help='Lista los respaldos disponibles')
        parser.add_argument('--confirmar', action='store_true', help='Confirma que se reemplazarán los datos')

    def _cliente_s3(self):
        from sgr_app.services.aws_service import get_s3_client

        if not settings.BACKUP_S3_BUCKET:
            raise CommandError('BACKUP_S3_BUCKET no está configurado.')
        return get_s3_client()

    def _listar(self):
        locales = sorted(Path(settings.BACKUP_DIR).glob('sgr-respaldo-*.gz'))
        self.stdout.write('Respaldos locales:')
        for ruta in locales[-20:]:
            self.stdout.write(f'  {ruta}')
        if settings.BACKUP_S3_BUCKET:
            respuesta = self._cliente_s3().list_objects_v2(
                Bucket=settings.BACKUP_S3_BUCKET, Prefix=settings.BACKUP_S3_PREFIX.rstrip('/') + '/',
            )
            self.stdout.write('Respaldos en S3:')
            for objeto in sorted(respuesta.get('Contents', []), key=lambda o: o['Key'])[-20:]:
                self.stdout.write(f'  {Path(objeto["Key"]).name}  {objeto["Size"] / 1024:.0f} KiB')

    def _descargar(self, nombre, carpeta):
        cliente = self._cliente_s3()
        clave = f'{settings.BACKUP_S3_PREFIX.rstrip("/")}/{Path(nombre).name}'
        destino = Path(carpeta) / Path(nombre).name
        cliente.download_file(settings.BACKUP_S3_BUCKET, clave, str(destino))
        esperado = cliente.head_object(Bucket=settings.BACKUP_S3_BUCKET, Key=clave).get('Metadata', {}).get('sha256')
        return destino, esperado

    def _restaurar_mysql(self, ruta):
        binario = shutil.which('mariadb') or shutil.which('mysql')
        if not binario:
            raise CommandError('No se encontró el cliente mariadb/mysql.')
        db = settings.DATABASES['default']
        comando = [
            binario, '--default-character-set=utf8mb4',
            f'--host={db.get("HOST") or "localhost"}', f'--port={db.get("PORT") or "3306"}',
            f'--user={db["USER"]}', db['NAME'],
        ]
        entorno = {**os.environ, 'MYSQL_PWD': db.get('PASSWORD', '')}
        with gzip.open(ruta, 'rb') as entrada:
            proceso = subprocess.Popen(  # nosec B603
                comando, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=entorno,
            )
            shutil.copyfileobj(entrada, proceso.stdin)
            proceso.stdin.close()
            errores = proceso.stderr.read()
            proceso.wait()
        if proceso.returncode != 0:
            raise CommandError(f'La restauración falló: {errores.decode("utf-8", "replace")[:500]}')

    def _restaurar_sqlite(self, ruta):
        destino = Path(settings.DATABASES['default']['NAME'])
        connection.close()
        temporal = destino.with_suffix('.restaurando')
        with gzip.open(ruta, 'rb') as entrada, open(temporal, 'wb') as salida:
            shutil.copyfileobj(entrada, salida)
        temporal.replace(destino)

    def handle(self, *args, **options):
        if options['listar']:
            self._listar()
            return
        if not (options['archivo'] or options['s3']):
            raise CommandError('Indica --archivo o --s3 (o usa --listar).')
        if not options['confirmar']:
            raise CommandError('La restauración REEMPLAZA los datos actuales. Repite con --confirmar.')

        inicio = time.monotonic()
        with tempfile.TemporaryDirectory() as carpeta:
            if options['s3']:
                ruta, esperado = self._descargar(options['s3'], carpeta)
            else:
                ruta = Path(options['archivo'])
                suma = ruta.with_name(ruta.name + '.sha256')
                esperado = suma.read_text(encoding='utf-8').split()[0] if suma.exists() else None
            if not ruta.exists():
                raise CommandError(f'No existe el respaldo {ruta}.')
            if esperado and _sha256(ruta) != esperado:
                raise CommandError('El SHA-256 del respaldo no coincide: archivo dañado o alterado.')
            if not esperado:
                self.stdout.write(self.style.WARNING('Respaldo sin SHA-256 registrado; no se pudo verificar.'))

            if connection.vendor == 'mysql':
                self._restaurar_mysql(ruta)
            elif connection.vendor == 'sqlite':
                self._restaurar_sqlite(ruta)
            else:
                raise CommandError(f'Motor no soportado: {connection.vendor}')

        minutos = (time.monotonic() - inicio) / 60
        self.stdout.write(self.style.SUCCESS(
            f'Restauración completada en {minutos:.1f} minutos. Reinicia el servicio: sudo systemctl restart sgr'
        ))
