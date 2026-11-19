"""Pruebas de rendimiento con volumen representativo (RNF-002).

"Condiciones normales" para esta prueba (supuesto documentado, ajustable):
- 8 delegaciones, 120 funcionarios, 8 ítems de medición por cargo.
- 4.800 actividades del trimestre, cada una con su evidencia.

Objetivos del RNF-002: registro y consulta <= 2 s; tableros consolidados <= 5 s.
Se ejecutan con el resto de la suite. Para medir contra MariaDB, correr con
USE_MYSQL=True apuntando a una base de pruebas:
    python manage.py test sgr_app.test_rendimiento
"""

import time
from datetime import timedelta

from django.test import Client, TestCase, tag

from sgr_app.models import Actividad, Delegacion, Evidencia, ItemMedicion, Usuario
from sgr_app import test_activity_workflows as base_workflows

LIMITE_REGISTRO_CONSULTA = 2.0
LIMITE_TABLERO = 5.0

DELEGACIONES = 8
FUNCIONARIOS = 120
ITEMS = 8
ACTIVIDADES_POR_FUNCIONARIO = 40


@tag('rendimiento')
class RendimientoTests(TestCase):
    setUp = base_workflows.ActivityWorkflowTests.setUp
    activity_form = base_workflows.ActivityWorkflowTests.activity_form

    @classmethod
    def setUpTestData(cls):
        cls.tiempos = {}

    def _poblar(self):
        delegaciones = [self.delegacion] + Delegacion.objects.bulk_create([
            Delegacion(nombre=f'Delegación carga {n}', codigo=f'D{n:02d}') for n in range(1, DELEGACIONES)
        ])
        items = [self.item] + ItemMedicion.objects.bulk_create([
            ItemMedicion(
                cargo=self.cargo, periodo=self.periodo, nombre_item=f'Ítem carga {n}',
                meta_cuantitativa=20, ponderador=0,
            )
            for n in range(1, ITEMS)
        ])
        funcionarios = Usuario.objects.bulk_create([
            Usuario(
                username=f'carga.{n:03d}', rol=Usuario.Rol.FUNCIONARIO, cargo=self.cargo,
                delegacion=delegaciones[n % DELEGACIONES], password='!',
            )
            for n in range(FUNCIONARIOS)
        ])
        dias = max((self.today - self.period_start).days, 0)
        actividades = []
        for f_idx, funcionario in enumerate(funcionarios):
            for a_idx in range(ACTIVIDADES_POR_FUNCIONARIO):
                actividades.append(Actividad(
                    funcionario=funcionario,
                    delegacion=funcionario.delegacion,
                    item_medicion=items[a_idx % ITEMS],
                    fecha_actividad=self.period_start + timedelta(days=(a_idx % (dias + 1))),
                    codigo_registro=f'PERF{f_idx:03d}{a_idx:04d}',
                    solicitud_problema='Carga de prueba',
                ))
        actividades = Actividad.objects.bulk_create(actividades, batch_size=1000)
        estados = [Evidencia.Estado.APROBADA, Evidencia.Estado.APROBADA, Evidencia.Estado.PENDIENTE,
                   Evidencia.Estado.RECHAZADA]
        Evidencia.objects.bulk_create([
            Evidencia(actividad=a, codigo_verificador=f'EVP-{n:06d}', estado=estados[n % len(estados)])
            for n, a in enumerate(actividades)
        ], batch_size=1000)

    def _medir(self, client, metodo, url, limite, **kwargs):
        inicio = time.perf_counter()
        response = getattr(client, metodo)(url, **kwargs)
        segundos = time.perf_counter() - inicio
        esperado = 302 if metodo == 'post' else 200
        self.assertEqual(response.status_code, esperado, f'{url} respondió {response.status_code}')
        self.assertLessEqual(
            segundos, limite, f'{metodo.upper()} {url} tardó {segundos:.2f} s (límite {limite} s)',
        )
        return segundos

    def test_tiempos_de_respuesta_con_volumen_representativo(self):
        self._poblar()
        supervisor = Client()
        supervisor.force_login(self.supervisor)
        coordinador = Usuario.objects.create_user(
            username='coordinador.carga', password='!', rol=Usuario.Rol.COORDINADOR,
        )
        comunal = Client()
        comunal.force_login(coordinador)
        delegado = Usuario.objects.create_user(
            username='delegado.carga', password='!', rol=Usuario.Rol.DELEGADO,
            delegacion=self.delegacion,
        )
        territorial = Client()
        territorial.force_login(delegado)

        mediciones = {
            # Tableros consolidados (<= 5 s)
            'GET /dashboard/ (comunal)': self._medir(comunal, 'get', '/dashboard/', LIMITE_TABLERO),
            'GET /dashboard/ (panel territorial)': self._medir(territorial, 'get', '/dashboard/', LIMITE_TABLERO),
            'GET /informes/ (delegado)': self._medir(territorial, 'get', '/informes/', LIMITE_TABLERO),
            'GET /semaforo/': self._medir(comunal, 'get', '/semaforo/', LIMITE_TABLERO),
            'GET /informes/': self._medir(comunal, 'get', '/informes/', LIMITE_TABLERO),
            'GET /alertas/': self._medir(comunal, 'get', '/alertas/', LIMITE_TABLERO),
            'GET /tubo/': self._medir(comunal, 'get', '/tubo/', LIMITE_TABLERO),
            # Registro y consulta (<= 2 s)
            'GET /personal/': self._medir(self.client, 'get', '/personal/', LIMITE_REGISTRO_CONSULTA),
            'POST registrar actividad': self._medir(
                self.client, 'post', '/actividad-diaria/registrar-actividad/',
                LIMITE_REGISTRO_CONSULTA, data=self.activity_form(),
            ),
            'GET /verificacion/': self._medir(supervisor, 'get', '/verificacion/', LIMITE_REGISTRO_CONSULTA),
            'GET /api/actividades/': self._medir(supervisor, 'get', '/api/actividades/', LIMITE_REGISTRO_CONSULTA),
        }
        resumen = ', '.join(f'{k}: {v:.2f}s' for k, v in mediciones.items())
        print(f'\n[RNF-002] {Actividad.objects.count()} actividades -> {resumen}')
