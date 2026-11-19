import hashlib
import io
import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

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


def sha256_archivo(archivo):
    """SHA-256 del contenido real del archivo (RN-010 / RNF-017)."""
    digest = hashlib.sha256()
    archivo.seek(0)
    for bloque in iter(lambda: archivo.read(64 * 1024), b''):
        digest.update(bloque)
    archivo.seek(0)
    return digest.hexdigest()


def _sanear_imagen(archivo, formato):
    """
    Vuelve a codificar la imagen con Pillow (RNF-009 / RNF-017).

    - Elimina EXIF/XMP, incluida la ubicación GPS del dispositivo.
    - Aplica la orientación EXIF a los píxeles para no girar la foto.
    - Descarta cualquier contenido ajeno a la imagen (datos tras el final del
      archivo, cargas ocultas en metadatos).
    El contenido saneado reemplaza al original en el mismo objeto subido.
    """
    archivo.seek(0)
    with Image.open(archivo) as original:
        imagen = ImageOps.exif_transpose(original)
        icc_profile = original.info.get('icc_profile')
        if formato == 'JPEG' and imagen.mode not in ('RGB', 'L'):
            imagen = imagen.convert('RGB')
        salida = io.BytesIO()
        opciones = {'icc_profile': icc_profile} if icc_profile else {}
        if formato == 'JPEG':
            imagen.save(salida, 'JPEG', quality=90, optimize=True, **opciones)
        elif formato == 'WEBP':
            imagen.save(salida, 'WEBP', quality=90, **opciones)
        else:
            imagen.save(salida, 'PNG', optimize=True, **opciones)
    datos = salida.getvalue()

    destino = archivo.file
    destino.seek(0)
    destino.truncate()
    destino.write(datos)
    if hasattr(destino, 'flush'):
        destino.flush()
    destino.seek(0)
    archivo.size = len(datos)


def metadatos_archivo_evidencia(archivo, subido_por):
    metadatos = validar_archivo_evidencia(archivo)
    if not getattr(archivo, '_sgr_saneado', False):
        _sanear_imagen(archivo, metadatos['formato'])
        archivo._sgr_saneado = True
        archivo._sgr_sha256 = sha256_archivo(archivo)
    metadatos['tamano_bytes'] = archivo.size
    metadatos['sha256'] = archivo._sgr_sha256
    metadatos['exif_eliminado'] = True
    if not getattr(archivo, '_sgr_storage_name_set', False):
        extension = FORMATOS_EVIDENCIA[metadatos['formato']][0]
        archivo.name = f'{uuid.uuid4().hex}{extension}'
        archivo._sgr_storage_name_set = True
    metadatos['subido_por'] = subido_por.get_username() if subido_por else None
    return metadatos


# CU-29 (A3): al registrar una actividad se adjuntan hasta tres fotografías.
MAX_FOTOS_POR_REGISTRO = 3
MENSAJE_MAX_FOTOS = f'Puedes adjuntar hasta {MAX_FOTOS_POR_REGISTRO} fotografías por registro.'
