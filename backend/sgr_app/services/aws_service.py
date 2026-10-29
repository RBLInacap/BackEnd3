"""
Módulo de Integración y Servicios Cloud AWS (Amazon Web Services)
Diseñado para la Ilustre Municipalidad de La Serena - Matriz SGR.
Satisface la arquitectura documentada en páginas 80-84 de Documentacion.pdf:
- Amazon S3: Almacenamiento desacoplado de evidencias fotográficas con Boto3.
- Amazon RDS: Motor de base de datos relacional MySQL/MariaDB.
"""

import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def is_s3_enabled() -> bool:
    """Verifica si el backend está configurado para utilizar Amazon S3."""
    return getattr(settings, 'USE_S3', False)


def get_s3_client():
    """Retorna un cliente boto3 configurado para Amazon S3."""
    try:
        import boto3
        return boto3.client(
            's3',
            aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
            aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
            region_name=getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
        )
    except Exception as e:
        logger.error(f"Error inicializando cliente AWS S3: {e}")
        return None


def test_s3_connection() -> dict:
    """
    Verifica la conectividad con el bucket de Amazon S3 configurado.
    Retorna un diccionario con el resultado de la prueba.
    """
    if not is_s3_enabled():
        return {
            'status': 'disabled',
            'message': 'Amazon S3 no está activado en las variables de entorno (USE_S3=False). Se usa almacenamiento local.',
            'bucket': None
        }

    bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
    client = get_s3_client()
    if not client:
        return {
            'status': 'error',
            'message': 'No se pudo inicializar el cliente Boto3. Verifique las credenciales AWS.',
            'bucket': bucket_name
        }

    try:
        client.head_bucket(Bucket=bucket_name)
        return {
            'status': 'connected',
            'message': f'Conexión exitosa con el bucket S3: {bucket_name}',
            'bucket': bucket_name
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Fallo al conectar con el bucket S3 "{bucket_name}": {str(e)}',
            'bucket': bucket_name
        }


def test_rds_connection() -> dict:
    """
    Verifica la conectividad con la base de datos MySQL (Amazon RDS o local).
    """
    use_mysql = getattr(settings, 'USE_MYSQL', False)
    db_config = settings.DATABASES.get('default', {})
    engine = db_config.get('ENGINE', '')

    if 'sqlite' in engine:
        return {
            'status': 'local_sqlite',
            'message': 'Base de datos activa en SQLite local para desarrollo.',
            'engine': engine,
            'host': 'localhost'
        }

    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION();")
            version = cursor.fetchone()
        return {
            'status': 'connected',
            'message': f'Conexión exitosa a Amazon RDS / MySQL: versión {version[0] if version else "desconocida"}',
            'engine': engine,
            'host': db_config.get('HOST', 'localhost'),
            'database': db_config.get('NAME')
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error conectando a MySQL / RDS: {str(e)}',
            'engine': engine,
            'host': db_config.get('HOST', 'localhost')
        }
