"""Pruebas del verificador de coherencia de datos (fase 1, solo lectura)."""

import json
from datetime import date, timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from sgr_app.models import (
    Actividad, CompromisoAgenda, Delegacion, EjecucionTarea, Evidencia, ItemMedicion, Cargo,
    PeriodoMedicion, Usuario,
)
from sgr_app.services.coherencia import ADVERTENCIA, CRITICO, INFO, verificar_coherencia


def codigos(hallazgos):
    return {h.codigo: h for h in hallazgos}


class VerificadorCoherenciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.delegacion = Delegacion.objects.create(nombre='Centro', codigo='CEN')
        cls.cargo = Cargo.objects.create(nombre='Gestor')
        cls.periodo = PeriodoMedicion.objects.create(
            nombre='T4', fecha_inicio=date(2026, 10, 1), fecha_termino=date(2026, 12, 31),
        )
        ItemMedicion.objects.create(
            cargo=cls.cargo, periodo=cls.periodo, nombre_item='Ítem', meta_cuantitativa=10, ponderador=100,
        )
        cls.funcionario = Usuario.objects.create_user(
            username='coh.func', email='coh.func@example.com', password='x', delegacion=cls.delegacion,
        )
        cls.actividad = Actividad.objects.create(
            funcionario=cls.funcionario, delegacion=cls.delegacion, fecha_actividad=timezone.localdate(),
        )

    def test_datos_coherentes_sin_hallazgos(self):
        self.assertEqual(verificar_coherencia(), [])

    def test_detecta_criticos(self):
        Usuario.objects.create_user(username='coh.dup', email='COH.FUNC@example.com', password='x')
        PeriodoMedicion.objects.filter(pk=self.periodo.pk).update(fecha_inicio=date(2027, 1, 1))
        ItemMedicion.objects.update(meta_cuantitativa=0)
        CompromisoAgenda.objects.create(
            delegacion=self.delegacion, responsable=self.funcionario, solicitante='V', estado='INVENTADO',
        )

        hallazgos = codigos(verificar_coherencia())

        for codigo in ('usuario.email_duplicado', 'periodo.fechas_invertidas', 'item.meta_no_positiva',
                       'compromiso.estado_invalido'):
            with self.subTest(codigo=codigo):
                self.assertEqual(hallazgos[codigo].severidad, CRITICO)
        self.assertEqual(hallazgos['usuario.email_duplicado'].cantidad, 2)

    def test_detecta_clave_huerfana(self):
        # SQLite verifica las claves foráneas al confirmar; dentro de la prueba se puede simular el caso.
        Actividad.objects.filter(pk=self.actividad.pk).update(delegacion_id=999999)
        # Django revisa las claves diferidas al cerrar la prueba: se restaura al terminar.
        self.addCleanup(
            Actividad.objects.filter(pk=self.actividad.pk).update, delegacion_id=self.delegacion.pk,
        )

        hallazgo = codigos(verificar_coherencia())['fk_huerfana.Actividad.delegacion']

        self.assertEqual(hallazgo.severidad, CRITICO)
        self.assertEqual(hallazgo.ejemplos, [self.actividad.pk])

    def test_detecta_advertencias_de_negocio(self):
        hoy = timezone.localdate()
        compromiso = CompromisoAgenda.objects.create(
            delegacion=self.delegacion, responsable=self.funcionario, solicitante='V',
        )
        CompromisoAgenda.objects.filter(pk=compromiso.pk).update(
            estado='REALIZADO', fecha_comprometida=hoy, fecha_compromiso=hoy - timedelta(days=5),
        )
        Evidencia.objects.create(actividad=self.actividad, estado=Evidencia.Estado.APROBADA)
        Evidencia.objects.filter(actividad=self.actividad).update(
            verificador=self.funcionario, fecha_validacion=timezone.now(),
        )
        ItemMedicion.objects.update(ponderador=80)

        hallazgos = codigos(verificar_coherencia())

        for codigo in ('compromiso.fechas_divergentes', 'compromiso.realizado_sin_fecha',
                       'evidencia.autovalidada', 'item.ponderacion_no_suma_100'):
            with self.subTest(codigo=codigo):
                self.assertEqual(hallazgos[codigo].severidad, ADVERTENCIA)

    def test_detecta_informativos(self):
        otra = Delegacion.objects.create(nombre='Rural', codigo='RUR')
        Actividad.objects.create(funcionario=self.funcionario, delegacion=otra, fecha_actividad=timezone.localdate())

        hallazgo = codigos(verificar_coherencia())['actividad.delegacion_distinta_al_funcionario']

        self.assertEqual(hallazgo.severidad, INFO)

    def test_no_modifica_datos(self):
        CompromisoAgenda.objects.create(
            delegacion=self.delegacion, responsable=self.funcionario, solicitante='V', estado='INVENTADO',
        )
        antes = list(CompromisoAgenda.objects.values())

        verificar_coherencia()

        self.assertEqual(list(CompromisoAgenda.objects.values()), antes)


@override_settings(ALERTAS_EMAIL=['alertas@example.com'])
class ComandoVerificarCoherenciaTests(TestCase):
    def setUp(self):
        self.delegacion = Delegacion.objects.create(nombre='Centro', codigo='CEN')
        self.usuario = Usuario.objects.create_user(username='cmd.coh', password='x')

    def _ejecutar(self, *args):
        salida = StringIO()
        try:
            call_command('verificar_coherencia', *args, stdout=salida)
            codigo = 0
        except SystemExit as exc:
            codigo = exc.code
        return codigo, salida.getvalue()

    def test_sin_problemas_sale_con_cero_y_registra_tarea(self):
        codigo, salida = self._ejecutar()
        self.assertEqual(codigo, 0)
        self.assertIn('Sin hallazgos', salida)
        self.assertTrue(EjecucionTarea.objects.get(nombre='verificar_coherencia').exito)

    def test_critico_sale_con_uno_y_alerta(self):
        CompromisoAgenda.objects.create(
            delegacion=self.delegacion, responsable=self.usuario, solicitante='V', estado='INVENTADO',
        )
        codigo, salida = self._ejecutar('--alertar')
        self.assertEqual(codigo, 1)
        self.assertIn('[CRITICO]', salida)
        self.assertIn('Coherencia de datos: 1 crítico(s)', mail.outbox[0].subject)

    def test_advertencia_solo_falla_en_modo_estricto(self):
        CompromisoAgenda.objects.create(delegacion=self.delegacion, responsable=self.usuario, solicitante='V')
        CompromisoAgenda.objects.update(estado='REALIZADO')
        self.assertEqual(self._ejecutar()[0], 0)
        self.assertEqual(self._ejecutar('--estricto')[0], 1)

    def test_salida_json(self):
        CompromisoAgenda.objects.create(delegacion=self.delegacion, responsable=self.usuario, solicitante='V')
        CompromisoAgenda.objects.update(estado='REALIZADO')
        _, salida = self._ejecutar('--json')
        datos = json.loads(salida)
        self.assertEqual(datos['resumen']['ADVERTENCIA'], 1)
        self.assertEqual(datos['hallazgos'][0]['codigo'], 'compromiso.realizado_sin_fecha')
