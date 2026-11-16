"""Pruebas de la Fase C: salud, monitoreo, disponibilidad, tareas y respaldos."""

import gzip
import json
import shutil
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from sgr_app.management.commands.monitorear_disponibilidad import Command as Monitor
from sgr_app.models import EjecucionTarea, Indisponibilidad
from sgr_app.services.disponibilidad import calcular_disponibilidad
from sgr_app.services.operacion import registrar_tarea, tareas_atrasadas

TZ = timezone.get_default_timezone()


def local(*args):
    return timezone.make_aware(datetime(*args), TZ)


class SaludTests(TestCase):
    def test_health_publico_y_sin_detalles_sensibles(self):
        response = Client().get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'estado': 'ok', 'checks': {'base_datos': 'ok', 'disco': 'ok'}})

    def test_health_responde_503_si_falla_la_base(self):
        with patch('sgr_app.services.operacion._chequear_base_datos', return_value=(False, 'OperationalError')):
            response = Client().get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['estado'], 'error')


@override_settings(ALERTAS_EMAIL=['alertas@example.com'])
class MonitorTests(TestCase):
    def setUp(self):
        self.carpeta = tempfile.mkdtemp()
        self.estado = Path(self.carpeta) / 'monitor.json'
        self.ajustes = override_settings(MONITOR_STATE_FILE=str(self.estado), MONITOR_GAP_SECONDS=300)
        self.ajustes.enable()

    def tearDown(self):
        self.ajustes.disable()
        shutil.rmtree(self.carpeta, ignore_errors=True)

    def _ejecutar(self, ok, cuando, causa='HTTP 503'):
        with patch.object(Monitor, 'comprobar_servicio', return_value=(ok, '' if ok else causa)), \
                patch('django.utils.timezone.now', return_value=cuando), \
                patch('sgr_app.management.commands.monitorear_disponibilidad.tareas_atrasadas', return_value=[]):
            call_command('monitorear_disponibilidad', stdout=StringIO())

    def test_registra_caida_con_inicio_fin_y_causa_y_alerta(self):
        t0 = timezone.now()
        self._ejecutar(True, t0)
        self._ejecutar(False, t0 + timedelta(minutes=1), causa='HTTP 503: base_datos')
        self._ejecutar(False, t0 + timedelta(minutes=2))
        self._ejecutar(True, t0 + timedelta(minutes=4))

        caida = Indisponibilidad.objects.get(tipo='CAIDA')
        self.assertEqual(caida.inicio, t0 + timedelta(minutes=1))
        self.assertEqual(caida.fin, t0 + timedelta(minutes=4))
        self.assertIn('base_datos', caida.causa)
        asuntos = [m.subject for m in mail.outbox]
        self.assertIn('[SGR] Sistema NO disponible', asuntos)
        self.assertIn('[SGR] Sistema recuperado', asuntos)

    def test_registra_periodo_sin_monitoreo_cuando_el_servidor_estuvo_apagado(self):
        t0 = timezone.now()
        self._ejecutar(True, t0)
        self._ejecutar(True, t0 + timedelta(hours=3))

        hueco = Indisponibilidad.objects.get(tipo='SIN_MONITOREO')
        self.assertEqual(hueco.duracion, timedelta(hours=3))

    def test_conserva_la_caida_en_archivo_si_la_base_no_responde(self):
        t0 = timezone.now()
        self._ejecutar(False, t0)
        self.assertEqual(json.loads(self.estado.read_text())['caida_desde'], t0.isoformat())
        self.assertFalse(Indisponibilidad.objects.exists())

    def test_alerta_tareas_atrasadas_una_vez_al_dia(self):
        t0 = timezone.now()
        with patch.object(Monitor, 'comprobar_servicio', return_value=(True, '')):
            for minuto in range(3):
                with patch('django.utils.timezone.now', return_value=t0 + timedelta(minutes=minuto)):
                    call_command('monitorear_disponibilidad', stdout=StringIO())
        atrasos = [m for m in mail.outbox if 'Tarea atrasada: respaldar_base_datos' in m.subject]
        self.assertEqual(len(atrasos), 1)


@override_settings(JORNADA_DIAS='0,1,2,3,4', JORNADA_HORAS='08:00-18:00', DISPONIBILIDAD_META_PCT=99.5)
class DisponibilidadTests(TestCase):
    def test_solo_cuenta_la_jornada_y_excluye_mantenimiento(self):
        # Noviembre 2026: 21 días hábiles x 10 h = 12.600 min.
        incidentes = [
            SimpleNamespace(inicio=local(2026, 11, 2, 9, 0), fin=local(2026, 11, 2, 10, 0), tipo='CAIDA'),
            # Fuera de jornada (domingo): no cuenta.
            SimpleNamespace(inicio=local(2026, 11, 1, 9, 0), fin=local(2026, 11, 1, 12, 0), tipo='CAIDA'),
            # Mantenimiento planificado: no cuenta ni como jornada ni como caída.
            SimpleNamespace(inicio=local(2026, 11, 3, 8, 0), fin=local(2026, 11, 3, 10, 0), tipo='MANTENIMIENTO'),
            SimpleNamespace(inicio=local(2026, 11, 3, 9, 0), fin=local(2026, 11, 3, 9, 30), tipo='CAIDA'),
        ]
        informe = calcular_disponibilidad(2026, 11, incidentes)

        self.assertEqual(informe.minutos_jornada, 12600 - 120)
        self.assertEqual(informe.minutos_no_disponible, 60)
        self.assertAlmostEqual(informe.disponibilidad_pct, (1 - 60 / 12480) * 100, places=3)
        self.assertTrue(informe.cumple_meta)

    def test_no_cumple_meta_con_caidas_largas(self):
        incidentes = [SimpleNamespace(inicio=local(2026, 11, 2, 8, 0), fin=local(2026, 11, 2, 18, 0), tipo='SIN_MONITOREO')]
        informe = calcular_disponibilidad(2026, 11, incidentes)
        self.assertFalse(informe.cumple_meta)

    def test_comando_informe(self):
        Indisponibilidad.objects.create(inicio=local(2026, 11, 2, 9, 0), fin=local(2026, 11, 2, 9, 30), causa='HTTP 503')
        salida = StringIO()
        call_command('informe_disponibilidad', mes='2026-11', stdout=salida)
        self.assertIn('Disponibilidad:', salida.getvalue())
        self.assertIn('HTTP 503', salida.getvalue())

    def test_registrar_mantenimiento(self):
        call_command('registrar_mantenimiento', inicio='2026-11-20 20:00', fin='2026-11-20 22:00',
                     motivo='Actualización', stdout=StringIO())
        self.assertTrue(Indisponibilidad.objects.filter(tipo='MANTENIMIENTO').exists())


@override_settings(ALERTAS_EMAIL=['alertas@example.com'])
class TareasTests(TestCase):
    def test_registra_exito(self):
        with registrar_tarea('respaldar_base_datos') as resultado:
            resultado['detalle'] = 'listo'
        tarea = EjecucionTarea.objects.get()
        self.assertTrue(tarea.exito)
        self.assertIsNotNone(tarea.fin)

    def test_registra_fallo_y_alerta(self):
        with self.assertRaises(RuntimeError), registrar_tarea('purgar_evidencias_vencidas'):
            raise RuntimeError('disco lleno')
        tarea = EjecucionTarea.objects.get()
        self.assertFalse(tarea.exito)
        self.assertIn('disco lleno', tarea.detalle)
        self.assertEqual(mail.outbox[0].subject, '[SGR] Falló la tarea purgar_evidencias_vencidas')

    def test_detecta_tareas_atrasadas(self):
        ahora = timezone.now()
        EjecucionTarea.objects.create(nombre='respaldar_base_datos', exito=True, fin=ahora - timedelta(hours=30))
        EjecucionTarea.objects.create(nombre='purgar_evidencias_vencidas', exito=True, fin=ahora - timedelta(hours=1))
        EjecucionTarea.objects.create(nombre='verificar_coherencia', exito=True, fin=ahora - timedelta(hours=2))
        nombres = [nombre for nombre, _ in tareas_atrasadas(ahora)]
        self.assertEqual(nombres, ['respaldar_base_datos'])

    def test_purga_queda_registrada(self):
        call_command('purgar_evidencias_vencidas', stdout=StringIO())
        self.assertTrue(EjecucionTarea.objects.get(nombre='purgar_evidencias_vencidas').exito)


class RespaldoTests(TestCase):
    def setUp(self):
        self.carpeta = tempfile.mkdtemp()
        self.base = Path(self.carpeta) / 'origen.sqlite3'
        import sqlite3
        conexion = sqlite3.connect(self.base)
        conexion.execute('CREATE TABLE prueba (valor TEXT)')
        conexion.execute("INSERT INTO prueba VALUES ('dato')")
        conexion.commit()
        conexion.close()

    def tearDown(self):
        shutil.rmtree(self.carpeta, ignore_errors=True)

    def test_genera_respaldo_comprimido_con_sha256_y_registro(self):
        destino = Path(self.carpeta) / 'respaldos'
        bases = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(self.base)}}
        with override_settings(BACKUP_DIR=str(destino), BACKUP_S3_BUCKET=''), \
                patch('sgr_app.management.commands.respaldar_base_datos.settings.DATABASES', bases):
            call_command('respaldar_base_datos', stdout=StringIO())

        archivo = next(destino.glob('sgr-respaldo-*.sqlite3.gz'))
        restaurado = Path(self.carpeta) / 'restaurado.sqlite3'
        restaurado.write_bytes(gzip.decompress(archivo.read_bytes()))
        import sqlite3
        self.assertEqual(sqlite3.connect(restaurado).execute('SELECT valor FROM prueba').fetchone(), ('dato',))
        self.assertTrue(archivo.with_name(archivo.name + '.sha256').exists())
        self.assertTrue(EjecucionTarea.objects.get(nombre='respaldar_base_datos').exito)

    def test_restaurar_exige_confirmacion(self):
        with self.assertRaises(CommandError):
            call_command('restaurar_base_datos', archivo='x.gz', stdout=StringIO())

    def test_restaurar_rechaza_respaldo_alterado(self):
        archivo = Path(self.carpeta) / 'sgr-respaldo-x.sqlite3.gz'
        archivo.write_bytes(gzip.compress(b'contenido'))
        archivo.with_name(archivo.name + '.sha256').write_text('0' * 64 + '  x\n')
        with self.assertRaisesMessage(CommandError, 'no coincide'):
            call_command('restaurar_base_datos', archivo=str(archivo), confirmar=True, stdout=StringIO())
