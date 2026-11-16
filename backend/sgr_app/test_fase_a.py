"""Pruebas de las correcciones de la Fase A (RNF-003, 007, 008, 009, 011, 012, 015, 017)."""

import shutil
import tempfile
from datetime import date
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from sgr_app.concurrencia import MENSAJE_CONFLICTO, huella
from sgr_app.db_auditoria import crear_triggers_auditoria
from sgr_app.models import (
    Actividad,
    Delegacion,
    Evidencia,
    PeriodoMedicion,
    RegistroAuditoria,
    Solicitante,
    Usuario,
)
from sgr_app.services.evidencias import metadatos_archivo_evidencia, sha256_archivo

PASSWORD = 'TestPassword123!'
TAG_GPS = 0x8825


def foto_con_gps(nombre='gps.jpg'):
    """JPEG con EXIF que incluye coordenadas GPS y modelo de cámara."""
    imagen = Image.new('RGB', (8, 8), color='green')
    exif = Image.Exif()
    exif[0x0110] = 'Telefono-Funcionario'               # Model
    exif[TAG_GPS] = {1: 'S', 2: (29.0, 54.0, 25.0), 3: 'W', 4: (71.0, 15.0, 7.0)}
    buffer = BytesIO()
    imagen.save(buffer, 'JPEG', exif=exif)
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/jpeg')


class SaneamientoEvidenciasTests(TestCase):
    """RNF-009 / RNF-017: EXIF eliminado y SHA-256 del contenido real."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls.media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls.media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = Usuario.objects.create_user(username='evid.test', password=PASSWORD)
        self.delegacion = Delegacion.objects.create(nombre='Delegación evidencias', codigo='EVD')
        self.actividad = Actividad.objects.create(
            funcionario=self.user,
            delegacion=self.delegacion,
            fecha_actividad=timezone.localdate(),
        )

    def test_elimina_exif_y_ubicacion_gps(self):
        archivo = foto_con_gps()
        with Image.open(BytesIO(archivo.read())) as original:
            self.assertIn(TAG_GPS, original.getexif())
        archivo.seek(0)

        metadatos = metadatos_archivo_evidencia(archivo, self.user)

        archivo.seek(0)
        with Image.open(archivo) as saneada:
            self.assertEqual(len(saneada.getexif()), 0)
        self.assertTrue(metadatos['exif_eliminado'])

    def test_sha256_corresponde_al_archivo_guardado(self):
        archivo = foto_con_gps()
        evidencia = Evidencia.objects.create(
            actividad=self.actividad,
            archivo_foto=archivo,
            subido_por=self.user,
            metadatos=metadatos_archivo_evidencia(archivo, self.user),
        )
        evidencia.refresh_from_db()

        with evidencia.archivo_foto.open('rb') as guardado:
            hash_real = sha256_archivo(guardado)
        self.assertEqual(evidencia.hash_sha256, hash_real)
        self.assertEqual(evidencia.calcular_sha256(), hash_real)
        self.assertTrue(evidencia.verificar_integridad())

    def test_detecta_archivo_alterado(self):
        archivo = foto_con_gps()
        evidencia = Evidencia.objects.create(
            actividad=self.actividad,
            archivo_foto=archivo,
            metadatos=metadatos_archivo_evidencia(archivo, self.user),
        )
        with open(evidencia.archivo_foto.path, 'ab') as manipulado:
            manipulado.write(b'contenido agregado')

        self.assertFalse(evidencia.verificar_integridad())

    def test_procesar_dos_veces_no_vuelve_a_comprimir(self):
        archivo = foto_con_gps()
        primero = metadatos_archivo_evidencia(archivo, self.user)
        segundo = metadatos_archivo_evidencia(archivo, self.user)
        self.assertEqual(primero['sha256'], segundo['sha256'])


@override_settings(TRUSTED_PROXY_COUNT=1)
class AuditoriaTests(TestCase):
    """RNF-008: IP real, campos obligatorios y protección en la base de datos."""

    def test_registra_la_ip_real_detras_de_nginx(self):
        Usuario.objects.create_user(username='ip.test', password=PASSWORD)

        Client().post(
            '/login/',
            {'username': 'ip.test', 'password': PASSWORD},
            REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_FOR='200.54.10.20',
        )

        registro = RegistroAuditoria.objects.get(accion='INICIO_SESION')
        self.assertEqual(registro.ip_origen, '200.54.10.20')

    def test_unifica_usuario_y_funcionario(self):
        user = Usuario.objects.create_user(username='aud.test', password=PASSWORD)
        registro = RegistroAuditoria.objects.create(usuario=user, accion='PRUEBA', entidad='X', identificador='5')
        self.assertEqual(registro.funcionario_id, user.pk)
        self.assertEqual(registro.identificador_registro, '5')

    def test_exige_entidad(self):
        with self.assertRaises(ValidationError):
            RegistroAuditoria.objects.create(accion='PRUEBA', entidad='')

    def test_triggers_impiden_modificar_o_borrar_por_sql(self):
        crear_triggers_auditoria(connection)
        RegistroAuditoria.objects.create(accion='PRUEBA', entidad='X', identificador='1')

        for sql in ("UPDATE Auditorias SET accion = 'ALTERADO'", 'DELETE FROM Auditorias'):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql)

        self.assertEqual(RegistroAuditoria.objects.filter(accion='PRUEBA').count(), 1)


class ConcurrenciaTests(TestCase):
    """RNF-003 / RNF-007: sin sobrescritura silenciosa al editar el mismo registro."""

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin.conc', email='admin.conc@example.com',
            password=PASSWORD, rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.delegacion = Delegacion.objects.create(nombre='Original', codigo='ORG')
        self.url = f'/mantenedores/delegaciones/{self.delegacion.pk}/modificar/'

    def _datos(self, nombre, huella_formulario):
        return {
            'nombre': nombre, 'codigo': 'ORG', 'direccion': '', 'email': '',
            'ambito': 'Urbano', 'telefono': '', '_huella': huella_formulario,
        }

    def test_formulario_incluye_la_huella(self):
        client = Client()
        client.force_login(self.admin)
        response = client.get(self.url)
        self.assertContains(response, f'name="_huella" value="{huella(self.delegacion)}"')

    def test_rechaza_guardar_sobre_un_registro_modificado_por_otra_persona(self):
        huella_al_abrir = huella(self.delegacion)
        # Otra persona guarda primero.
        Delegacion.objects.filter(pk=self.delegacion.pk).update(nombre='Cambio de otra persona')

        client = Client()
        client.force_login(self.admin)
        response = client.post(self.url, self._datos('Mi cambio', huella_al_abrir))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Otra persona modificó este registro')
        self.delegacion.refresh_from_db()
        self.assertEqual(self.delegacion.nombre, 'Cambio de otra persona')
        self.assertFalse(
            RegistroAuditoria.objects.filter(entidad='Delegacion', accion='MODIFICACION').exists()
        )

    def test_guarda_si_nadie_modifico_el_registro(self):
        client = Client()
        client.force_login(self.admin)
        response = client.post(self.url, self._datos('Nuevo nombre', huella(self.delegacion)))

        self.assertEqual(response.status_code, 302)
        self.delegacion.refresh_from_db()
        self.assertEqual(self.delegacion.nombre, 'Nuevo nombre')

    def test_api_entrega_etag_y_rechaza_if_match_obsoleto(self):
        actividad = Actividad.objects.create(
            funcionario=self.admin, delegacion=self.delegacion, fecha_actividad=timezone.localdate(),
        )
        client = Client()
        client.force_login(self.admin)
        url = f'/api/actividades/{actividad.pk}/'

        etag = client.get(url)['ETag']
        self.assertEqual(etag, f'"{huella(actividad)}"')

        Actividad.objects.filter(pk=actividad.pk).update(accion='editado por otra persona')
        response = client.patch(
            url, {'accion': 'mi cambio'}, content_type='application/json', HTTP_IF_MATCH=etag,
        )

        self.assertEqual(response.status_code, 412)
        self.assertEqual(response.json()['detail'], MENSAJE_CONFLICTO)
        actividad.refresh_from_db()
        self.assertEqual(actividad.accion, 'editado por otra persona')


class PaginacionTests(TestCase):
    """RNF-011: la paginación conserva los filtros activos."""

    def test_conserva_filtro_de_inactivos(self):
        admin = Usuario.objects.create_superuser(
            username='admin.pag', email='admin.pag@example.com',
            password=PASSWORD, rol=Usuario.Rol.ADMINISTRADOR,
        )
        for numero in range(30):
            Solicitante.objects.create(nombre=f'Inactivo {numero:02d}', estado=False)
        client = Client()
        client.force_login(admin)

        response = client.get('/mantenedores/solicitantes/?estado=inactivas')

        self.assertContains(response, 'estado=inactivas&amp;page=2')


class UmbralesConfigurablesTests(TestCase):
    """RNF-015: el cumplimiento de compromisos usa el umbral colectivo del período."""

    def test_tablero_usa_umbral_colectivo_del_periodo(self):
        admin = Usuario.objects.create_superuser(
            username='admin.umbral', email='admin.umbral@example.com',
            password=PASSWORD, rol=Usuario.Rol.ADMINISTRADOR,
        )
        hoy = timezone.localdate()
        periodo = PeriodoMedicion.objects.create(
            nombre='Período umbral',
            fecha_inicio=date(hoy.year, 1, 1),
            fecha_termino=date(hoy.year, 12, 31),
            umbral_colectivo=0.0,
        )
        client = Client()
        client.force_login(admin)

        response = client.get(f'/tubo/?periodo={periodo.pk}')

        self.assertEqual(response.status_code, 200)
