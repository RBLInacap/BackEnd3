"""Monitor de disponibilidad, capacidad y tareas (RNF-001 / RNF-018).

Pensado para ejecutarse cada minuto con un temporizador de systemd.

- Consulta /health/ por HTTP (atraviesa Nginx/Gunicorn/Django/BD).
- Registra cada caída con inicio, fin y causa (modelo Indisponibilidad).
- Detecta periodos en que el servidor estuvo apagado o el monitor no corrió.
- Alerta por correo al caer y al recuperarse, por disco/memoria altos y por
  tareas programadas atrasadas (máximo una alerta diaria por motivo).

El estado entre ejecuciones se guarda en un archivo JSON, para no perder la
hora de inicio de una caída aunque la base de datos esté caída.
"""

import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from sgr_app.services.operacion import (
    enviar_alerta,
    memoria_disponible_pct,
    tareas_atrasadas,
    uso_disco_pct,
)

MAX_PENDIENTES = 500


def _iso(valor):
    return valor.isoformat() if valor else None


def _fecha(valor):
    return datetime.fromisoformat(valor) if valor else None


class Command(BaseCommand):
    help = 'Comprueba la disponibilidad del SGR y registra caídas y alertas.'

    # --- estado persistente --------------------------------------------------

    def _ruta_estado(self):
        return Path(settings.MONITOR_STATE_FILE)

    def _leer_estado(self):
        try:
            return json.loads(self._ruta_estado().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _guardar_estado(self, estado):
        ruta = self._ruta_estado()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix('.tmp')
        temporal.write_text(json.dumps(estado), encoding='utf-8')
        temporal.replace(ruta)

    # --- comprobación ----------------------------------------------------------

    def comprobar_servicio(self):
        """Devuelve (ok, causa)."""
        url = settings.MONITOR_URL
        cabeceras = {'User-Agent': 'sgr-monitor'}
        if settings.MONITOR_HOST:
            cabeceras['Host'] = settings.MONITOR_HOST
        if url.startswith('http://127.0.0.1') or url.startswith('http://localhost'):
            # Directo a Gunicorn: se indica que la petición original era HTTPS
            # para que Django no redirija.
            cabeceras['X-Forwarded-Proto'] = 'https'
        contexto = None
        if url.startswith('https://') and settings.MONITOR_CA_FILE:
            contexto = ssl.create_default_context(cafile=settings.MONITOR_CA_FILE)
        solicitud = urllib.request.Request(url, headers=cabeceras)
        try:
            # URL fija definida por el administrador en la configuración.
            with urllib.request.urlopen(  # nosec B310
                solicitud, timeout=settings.MONITOR_TIMEOUT, context=contexto,
            ) as respuesta:
                cuerpo = json.loads(respuesta.read().decode('utf-8') or '{}')
                if respuesta.status == 200 and cuerpo.get('estado') == 'ok':
                    return True, ''
                return False, f'HTTP {respuesta.status}: {cuerpo.get("checks")}'
        except urllib.error.HTTPError as exc:
            try:
                checks = json.loads(exc.read().decode('utf-8')).get('checks')
            except (ValueError, AttributeError):
                checks = None
            return False, f'HTTP {exc.code}' + (f': {checks}' if checks else '')
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return False, f'Sin respuesta: {type(exc).__name__}'

    # --- registro de incidentes ---------------------------------------------------

    def _guardar_incidentes(self, estado):
        """Intenta guardar en BD los incidentes pendientes; conserva los que fallen."""
        from sgr_app.models import Indisponibilidad

        pendientes = estado.get('pendientes', [])
        restantes = []
        for incidente in pendientes:
            try:
                Indisponibilidad.objects.create(
                    inicio=_fecha(incidente['inicio']),
                    fin=_fecha(incidente['fin']),
                    tipo=incidente['tipo'],
                    causa=incidente.get('causa', '')[:2000],
                )
            except Exception:
                restantes.append(incidente)
        estado['pendientes'] = restantes[-MAX_PENDIENTES:]

    def _alerta_diaria(self, estado, clave, asunto, mensaje, ahora):
        enviadas = estado.setdefault('alertas', {})
        ultima = _fecha(enviadas.get(clave))
        if ultima is None or ahora - ultima >= timedelta(hours=24):
            enviar_alerta(asunto, mensaje)
            enviadas[clave] = _iso(ahora)

    def handle(self, *args, **options):
        ahora = timezone.now()
        estado = self._leer_estado()
        estado.setdefault('pendientes', [])

        # 1. Huecos: el monitor no corrió (servidor apagado, timer detenido).
        ultimo = _fecha(estado.get('ultimo_chequeo'))
        hueco = timedelta(seconds=settings.MONITOR_GAP_SECONDS)
        if ultimo and ahora - ultimo > hueco:
            estado['pendientes'].append({
                'inicio': _iso(ultimo), 'fin': _iso(ahora), 'tipo': 'SIN_MONITOREO',
                'causa': 'Servidor apagado o monitor detenido (sin chequeos en el periodo).',
            })
            enviar_alerta(
                'Periodo sin monitoreo',
                f'No hubo chequeos entre {ultimo:%Y-%m-%d %H:%M} y {ahora:%Y-%m-%d %H:%M} (UTC).',
            )

        # 2. Disponibilidad del servicio.
        ok, causa = self.comprobar_servicio()
        caida_desde = _fecha(estado.get('caida_desde'))
        if not ok and caida_desde is None:
            estado['caida_desde'] = _iso(ahora)
            estado['causa'] = causa
            enviar_alerta('Sistema NO disponible', f'Desde {ahora:%Y-%m-%d %H:%M} UTC. Causa: {causa}')
        elif ok and caida_desde is not None:
            estado['pendientes'].append({
                'inicio': _iso(caida_desde), 'fin': _iso(ahora), 'tipo': 'CAIDA',
                'causa': estado.get('causa', ''),
            })
            minutos = round((ahora - caida_desde).total_seconds() / 60, 1)
            enviar_alerta('Sistema recuperado', f'Estuvo no disponible {minutos} minutos. Causa: {estado.get("causa", "")}')
            estado['caida_desde'] = None
            estado['causa'] = ''

        if ok:
            self._guardar_incidentes(estado)

            # 3. Capacidad.
            disco = uso_disco_pct()
            if disco >= settings.MONITOR_DISK_ALERT_PCT:
                self._alerta_diaria(estado, 'disco', 'Disco casi lleno', f'Uso de disco: {disco} %.', ahora)
            memoria = memoria_disponible_pct()
            if memoria is not None and memoria < settings.MONITOR_MEM_ALERT_PCT:
                self._alerta_diaria(estado, 'memoria', 'Memoria baja', f'Memoria disponible: {memoria} %.', ahora)

            # 4. Tareas programadas (respaldo, purga).
            for nombre, ultimo_exito in tareas_atrasadas(ahora):
                cuando = f'{ultimo_exito:%Y-%m-%d %H:%M} UTC' if ultimo_exito else 'nunca'
                self._alerta_diaria(
                    estado, f'tarea:{nombre}', f'Tarea atrasada: {nombre}',
                    f'Último resultado exitoso: {cuando}.', ahora,
                )

        estado['ultimo_chequeo'] = _iso(ahora)
        self._guardar_estado(estado)
        self.stdout.write('ok' if ok else f'NO DISPONIBLE: {causa}')
