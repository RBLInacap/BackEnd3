import re

from django.core.exceptions import ValidationError


_RUT_PATTERN = re.compile(r'^(?P<body>(?:\d{1,2}\.)?\d{3}\.\d{3}|\d{7,8})-(?P<dv>[\dkK])$')
_FORMATTED_RUT_PATTERN = re.compile(r'^(?P<body>\d{1,2}\.\d{3}\.\d{3})-(?P<dv>[\dkK])$')
_PHONE_PATTERN = re.compile(
    r'^(?:\+56[\s.-]?9(?:[\s.-]?[0-9]){8}|9(?:[\s.-]?[0-9]){8}|'
    r'[0-9](?:[\s.-]?[0-9]){7})$'
)


def normalize_chilean_rut(value):
    rut = (value or '').strip()
    match = _RUT_PATTERN.fullmatch(rut)
    if not match:
        raise ValidationError('Ingresa el RUN en formato 12.345.678-5.')

    body = match.group('body').replace('.', '')
    dv = match.group('dv').upper()
    if not 7 <= len(body) <= 8 or body.startswith('0'):
        raise ValidationError('El RUN debe tener entre 7 y 8 dígitos antes del guion.')

    total = 0
    multiplier = 2
    for digit in reversed(body):
        total += int(digit) * multiplier
        multiplier = 2 if multiplier == 7 else multiplier + 1

    remainder = 11 - (total % 11)
    expected_dv = 'K' if remainder == 10 else '0' if remainder == 11 else str(remainder)
    if dv != expected_dv:
        raise ValidationError('El dígito verificador del RUN no es válido.')

    grouped_body = f'{int(body):,}'.replace(',', '.')
    return f'{grouped_body}-{dv}'


def validate_chilean_rut(value):
    if value:
        rut = (value or '').strip()
        if not _FORMATTED_RUT_PATTERN.fullmatch(rut):
            raise ValidationError('Ingresa el RUN con puntos y guion, por ejemplo 12.345.678-5.')
        normalize_chilean_rut(rut)


def normalize_chilean_phone(value):
    phone = (value or '').strip()
    if not phone:
        return ''

    if not _PHONE_PATTERN.fullmatch(phone):
        raise ValidationError('Ingresa 8 dígitos para un teléfono móvil chileno con prefijo +56 9.')

    clean_phone = re.sub(r'[\s.-]', '', phone)
    if clean_phone.startswith('+569'):
        subscriber_number = clean_phone[4:]
    elif len(clean_phone) == 9:
        subscriber_number = clean_phone[1:]
    else:
        subscriber_number = clean_phone

    if len(set(f'9{subscriber_number}')) == 1:
        raise ValidationError('Ingresa un número telefónico válido; no se aceptan números repetidos.')

    return f'+569{subscriber_number}'


def validate_chilean_phone(value):
    if value:
        normalize_chilean_phone(value)
