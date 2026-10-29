import re

from django.core.exceptions import ValidationError


_RUT_PATTERN = re.compile(r'^(?P<body>(?:\d{1,2}\.)?\d{3}\.\d{3}|\d{7,8})-(?P<dv>[\dkK])$')
_FORMATTED_RUT_PATTERN = re.compile(r'^(?P<body>\d{1,2}\.\d{3}\.\d{3})-(?P<dv>[\dkK])$')
_PHONE_PATTERN = re.compile(r'^(?:\+56[\s.-]?)?[2-79](?:[\s.-]?\d){8}$')


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
        raise ValidationError('Usa un teléfono chileno local de 9 dígitos o el prefijo +56.')

    digits = re.sub(r'\D', '', phone)
    if phone.startswith('+56'):
        digits = digits[2:]
    if len(digits) != 9 or digits[0] not in '2345679':
        raise ValidationError('El número debe tener 9 dígitos y comenzar con 9 (móvil) o un prefijo fijo chileno válido.')
    if len(set(digits)) == 1:
        raise ValidationError('Ingresa un número telefónico asignable; no se aceptan números repetidos.')
    return f'+56{digits}'


def validate_chilean_phone(value):
    if value:
        normalize_chilean_phone(value)
