import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

FORMATOS_EVIDENCIA = {
    'JPEG': ('.jpg', 'image/jpeg'),
    'PNG': ('.png', 'image/png'),
    'WEBP': ('.webp', 'image/webp'),
}


def validar_archivo_evidencia(archivo):
    if not archivo:
        raise ValidationError('Selecciona un archivo de imagen como evidencia.')

    if archivo.size > settings.EVIDENCE_MAX_FILE_SIZE:
        limite_mib = settings.EVIDENCE_MAX_FILE_SIZE / (1024 * 1024)
        raise ValidationError(
            f'Cada fotografía debe pesar como máximo {limite_mib:g} MiB.'
        )

    try:
        archivo.seek(0)
        with Image.open(archivo) as imagen:
            formato = (imagen.format or '').upper()
            if imagen.width * imagen.height > settings.EVIDENCE_MAX_IMAGE_PIXELS:
                raise ValidationError(
                    'La resolución de la imagen supera el máximo permitido.'
                )
            imagen.verify()
    except ValidationError:
        raise
    except (
        Image.DecompressionBombError,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise ValidationError('El archivo no contiene una imagen válida.') from exc
    finally:
        archivo.seek(0)

    formatos_permitidos = set(settings.EVIDENCE_ALLOWED_FORMATS)
    if formato not in formatos_permitidos:
        permitidos = ', '.join(sorted(formatos_permitidos))
        raise ValidationError(
            f'Formato de imagen no permitido. Formatos aceptados: {permitidos}.'
        )

    return {
        'nombre_archivo': os.path.basename((archivo.name or '').replace('\\', '/')),
        'formato': formato,
        'mime_type': FORMATOS_EVIDENCIA.get(formato, ('', ''))[1],
        'tamano_bytes': archivo.size,
    }


def metadatos_archivo_evidencia(archivo, subido_por):
    metadatos = validar_archivo_evidencia(archivo)
    if not getattr(archivo, '_sgr_storage_name_set', False):
        extension = FORMATOS_EVIDENCIA[metadatos['formato']][0]
        archivo.name = f'{uuid.uuid4().hex}{extension}'
        archivo._sgr_storage_name_set = True
    metadatos['subido_por'] = subido_por.get_username() if subido_por else None
    return metadatos
