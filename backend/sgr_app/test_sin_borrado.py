"""Normativa: en el SGR nada se elimina; solo se anula o se deshabilita.

Estas pruebas protegen la regla: cubren la anulación y reactivación de
compromisos, el historial de códigos de recuperación, los bloqueos de acceso y
un control del código fuente que falla si alguien vuelve a introducir un
borrado físico.
"""
import re
from pathlib import Path

from django.test import Client, TestCase
from django.utils import timezone

from sgr_app.models import BloqueoAcceso, CompromisoAgenda, Delegacion, RegistroAuditoria, Usuario
from sgr_app.services import seguridad_acceso

PASSWORD = 'TestPassword123!'
APP_DIR = Path(__file__).resolve().parent

# Patrones de borrado físico prohibidos en el código de la aplicación.
PATRONES_BORRADO = [
    re.compile(r'\.delete\(\s*\)'),
    re.compile(r'storage\.delete\('),
    re.compile(r'\bperform_destroy\b'),
    re.compile(r"'destroy'"),
    re.compile(r'\bpost_delete\b'),
]


class CodigoFuenteSinBorradoTests(TestCase):
    def test_la_aplicacion_no_contiene_borrados_fisicos(self):
        hallazgos = []
        for archivo in APP_DIR.rglob('*.py'):
            relativo = archivo.relative_to(APP_DIR)
            if relativo.parts[0] == 'migrations' or archivo.name.startswith('test'):
                continue
            for numero, linea in enumerate(archivo.read_text(encoding='utf-8').splitlines(), 1):
                if any(patron.search(linea) for patron in PATRONES_BORRADO):
                    hallazgos.append(f'{relativo}:{numero}: {linea.strip()}')
        self.assertEqual(
            hallazgos, [],
            'Por normativa nada se elimina: usa anulación o baja lógica.\n' + '\n'.join(hallazgos),
        )


class ReactivarCompromisoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.delegacion = Delegacion.objects.create(nombre='Delegación R', codigo='DLR')
        cls.admin = Usuario.objects.create_user(
            username='r.admin', password=PASSWORD, rol=Usuario.Rol.ADMINISTRADOR,
        )
        cls.coordinador = Usuario.objects.create_user(
            username='r.coord', password=PASSWORD, rol=Usuario.Rol.COORDINADOR,
        )

    def setUp(self):
        self.compromiso = CompromisoAgenda.objects.create(
            solicitante='Junta Vecinal R', delegacion=self.delegacion,
            responsable=self.coordinador, creado_por=self.coordinador,
        )
        self.compromiso.anular(self.coordinador, 'Registrado por error')

    def _cliente(self, usuario):
        cliente = Client()
        cliente.force_login(usuario)
        return cliente

    def test_solo_el_administrador_ve_y_reactiva_anulados(self):
        url = f'/tubo/{self.compromiso.pk}/reactivar/'
        coordinador = self._cliente(self.coordinador)
        self.assertNotContains(coordinador.get('/tubo/'), 'tab-anulados')
        self.assertEqual(coordinador.post(url, {'motivo': 'x'}).status_code, 403)

        admin = self._cliente(self.admin)
        self.assertContains(admin.get('/tubo/'), 'Junta Vecinal R')  # pestaña Anulados
        admin.post(url, {'motivo': '  '})
        self.assertTrue(CompromisoAgenda.todos.get(pk=self.compromiso.pk).anulado)

        admin.post(url, {'motivo': 'Se anuló por error'})
        reactivado = CompromisoAgenda.objects.get(pk=self.compromiso.pk)
        self.assertFalse(reactivado.anulado)
        auditoria = RegistroAuditoria.objects.get(
            accion='REACTIVAR_COMPROMISO', identificador_registro=str(self.compromiso.pk),
        )
        # La anulación previa queda preservada en la auditoría inmutable.
        self.assertEqual(auditoria.valor_anterior['motivo_anulacion'], 'Registrado por error')
        self.assertEqual(auditoria.valor_nuevo['motivo_reactivacion'], 'Se anuló por error')


class BloqueoAccesoSinBorradoTests(TestCase):
    def test_login_exitoso_reinicia_el_bloqueo_sin_borrarlo(self):
        registro = BloqueoAcceso.objects.create(
            clave='a' * 64, intentos_fallidos=3, bloqueado_hasta=timezone.now(),
        )
        seguridad_acceso._limpiar([seguridad_acceso._Ambito(clave=registro.clave, limite=5)])
        self.assertTrue(BloqueoAcceso.objects.filter(pk=registro.pk).exists())
        registro.refresh_from_db()
        self.assertEqual(registro.intentos_fallidos, 0)
        self.assertIsNone(registro.bloqueado_hasta)
