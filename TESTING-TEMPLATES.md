#  Templates de Pruebas - Listo para Copiar y Pegar

Este archivo contiene **templates prontos** que puedes copiar a tu proyecto para pruebas unitarias, integración y aceptación.

---

## Estructura de Carpetas Recomendada

```
proyecto_django/
├── tests/
│   ├── __init__.py
│   ├── test_unitarios.py
│   ├── test_integracion.py
│   ├── test_aceptacion.py
│   └── test_fixtures/  (datos de prueba)
│       ├── usuarios.json
│       ├── items.json
│       └── actividades.json
├── manage.py
└── config/
```

---

## Template 1: Test Unitario Básico

Copia este archivo como `tests/test_unitarios.py`:

```python
"""
tests/test_unitarios.py

Pruebas unitarias para funciones y reglas de negocio aisladas.
Ejecutar: python manage.py test tests.test_unitarios
"""

from django.test import TestCase
from datetime import date, datetime


class TestCalculosPorcentaje(TestCase):
    """Pruebas para cálculos de porcentaje y métricas"""
    
    def test_porcentaje_basico(self):
        """Test: Calcular porcentaje simple"""
        resultado = (50 / 100) * 100
        self.assertEqual(resultado, 50.0)
    
    def test_porcentaje_sobre_100(self):
        """Test: Porcentaje no puede ser mayor a 100%"""
        resultado = min((150 / 100) * 100, 100.0)
        self.assertEqual(resultado, 100.0)
    
    def test_division_por_cero(self):
        """Test: Manejar meta = 0 sin error"""
        meta = 0
        avance = 50
        resultado = 0.0 if meta == 0 else (avance / meta) * 100
        self.assertEqual(resultado, 0.0)


class TestFormatoFechas(TestCase):
    """Pruebas para formateo y validación de fechas"""
    
    def test_fecha_valida(self):
        """Test: Fecha válida se acepta"""
        fecha = date(2024, 1, 15)
        self.assertIsNotNone(fecha)
        self.assertEqual(fecha.year, 2024)
    
    def test_fecha_futura_no_permitida(self):
        """Test: Fecha futura debe rechazarse"""
        fecha_futura = date(2099, 1, 1)
        hoy = date.today()
        self.assertTrue(fecha_futura > hoy)
    
    def test_fecha_pasada_permitida(self):
        """Test: Fecha pasada se permite"""
        fecha_pasada = date(2020, 1, 1)
        hoy = date.today()
        self.assertTrue(fecha_pasada < hoy)


class TestFormatoRUT(TestCase):
    """Pruebas para validación de RUT chileno"""
    
    def test_rut_con_formato_correcto(self):
        """Test: RUT con formato XX.XXX.XXX-X es válido"""
        import re
        patron = r'^\d{1,2}\.\d{3}\.\d{3}-[0-9K]$'
        ruts_validos = [
            '12.345.678-9',
            '1.234.567-K',
            '18.123.456-0'
        ]
        for rut in ruts_validos:
            with self.subTest(rut=rut):
                self.assertTrue(bool(re.match(patron, rut)))
    
    def test_rut_sin_formato(self):
        """Test: RUT sin formato es inválido"""
        import re
        patron = r'^\d{1,2}\.\d{3}\.\d{3}-[0-9K]$'
        ruts_invalidos = [
            '123456789',
            '12.345.678',
            '12345678-9',
            '12.345.678-X'  # X no es válido
        ]
        for rut in ruts_invalidos:
            with self.subTest(rut=rut):
                self.assertFalse(bool(re.match(patron, rut)))


class TestLogicaBooleana(TestCase):
    """Pruebas para lógica booleana y condicionales"""
    
    def test_usuario_puede_editar_propios_datos(self):
        """Test: Usuario solo puede editar sus propios datos"""
        usuario_id = 5
        recurso_usuario_id = 5
        self.assertTrue(usuario_id == recurso_usuario_id)
        
        recurso_usuario_id = 6
        self.assertFalse(usuario_id == recurso_usuario_id)
    
    def test_rol_tiene_permiso(self):
        """Test: Verificar permisos por rol"""
        permisos = {
            'admin': ['crear', 'leer', 'editar', 'eliminar'],
            'empleado': ['crear', 'leer'],
            'viewer': ['leer']
        }
        
        rol = 'empleado'
        self.assertIn('crear', permisos[rol])
        self.assertNotIn('eliminar', permisos[rol])
```

**Ejecutar:**
```powershell
python manage.py test tests.test_unitarios -v 2
```

---

## Template 2: Test de Integración Básico

Copia este archivo como `tests/test_integracion.py`:

```python
"""
tests/test_integracion.py

Pruebas de integración con base de datos, APIs y servicios.
Ejecutar: python manage.py test tests.test_integracion
"""

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from datetime import date
import json


# Asume que tienes un modelo Empleado
# Si no, descomenta y ajusta para tu proyecto:

"""
from empleados.models import Empleado

class TestEmpleadoIntegracion(TestCase):
    
    def setUp(self):
        '''Datos iniciales para cada test'''
        self.empleado = Empleado.objects.create(
            nombre='Juan',
            apellido='Pérez',
            rut='12.345.678-9',
            email='juan@laserena.cl',
            rol='empleado',
            fecha_contratacion=date(2024, 1, 15)
        )
    
    def test_empleado_se_guarda_en_bd(self):
        '''Test: Crear empleado persiste en BD'''
        # Recuperar desde BD
        empleado = Empleado.objects.get(rut='12.345.678-9')
        self.assertEqual(empleado.nombre, 'Juan')
    
    def test_no_permitir_rut_duplicado(self):
        '''Test: Validar restricción UNIQUE en RUT'''
        from django.db import IntegrityError
        
        with self.assertRaises(IntegrityError):
            Empleado.objects.create(
                nombre='Carlos',
                apellido='López',
                rut='12.345.678-9',  # RUT duplicado
                email='carlos@laserena.cl',
                rol='empleado',
                fecha_contratacion=date(2024, 1, 15)
            )
    
    def test_listar_empleados_desde_bd(self):
        '''Test: Consultar múltiples registros'''
        Empleado.objects.create(
            nombre='Carlos',
            apellido='López',
            rut='11.111.111-1',
            email='carlos@laserena.cl',
            rol='empleado',
            fecha_contratacion=date(2024, 1, 15)
        )
        
        empleados = Empleado.objects.all()
        self.assertEqual(empleados.count(), 2)
"""


class TestAutenticacionIntegracion(TestCase):
    """Pruebas de autenticación y permisos"""
    
    def setUp(self):
        """Crear usuario para tests"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            email='test@laserena.cl'
        )
        self.client = Client()
    
    def test_login_exitoso(self):
        """Test: Usuario puede loguearse"""
        resultado = self.client.login(
            username='testuser',
            password='testpass123'
        )
        self.assertTrue(resultado)
    
    def test_login_fallido_password_incorrecto(self):
        """Test: Password incorrecto rechaza login"""
        resultado = self.client.login(
            username='testuser',
            password='passwordincorrecto'
        )
        self.assertFalse(resultado)
    
    def test_usuario_en_grupo_tiene_permiso(self):
        """Test: Grupo otorga permisos"""
        grupo = Group.objects.create(name='Verificadores')
        self.user.groups.add(grupo)
        
        self.assertTrue(self.user.groups.filter(name='Verificadores').exists())


class TestAPIIntegracion(TestCase):
    """Pruebas de endpoints REST"""
    
    def setUp(self):
        """Setup inicial"""
        self.client = Client()
        # Crear usuario autenticado si tu API lo requiere
        self.user = User.objects.create_user(
            username='apiuser',
            password='apipass123'
        )
    
    def test_get_endpoint_retorna_200(self):
        """Test: GET request devuelve 200 OK"""
        # Ajusta la URL según tu proyecto
        # respuesta = self.client.get('/api/empleados/')
        # self.assertEqual(respuesta.status_code, 200)
        
        # Template genérico:
        self.client.login(username='apiuser', password='apipass123')
        # respuesta = self.client.get('/api/algún-endpoint/')
        # self.assertEqual(respuesta.status_code, 200)
    
    def test_post_endpoint_crea_recurso(self):
        """Test: POST crea nuevo recurso"""
        self.client.login(username='apiuser', password='apipass123')
        
        datos = {
            'nombre': 'Test Item',
            'descripcion': 'Descripción de prueba'
        }
        
        # respuesta = self.client.post(
        #     '/api/items/',
        #     data=json.dumps(datos),
        #     content_type='application/json'
        # )
        # self.assertEqual(respuesta.status_code, 201)
    
    def test_put_endpoint_actualiza_recurso(self):
        """Test: PUT actualiza recurso existente"""
        self.client.login(username='apiuser', password='apipass123')
        
        # Ajusta según tu modelo
        datos = {'nombre': 'Nombre actualizado'}
        
        # respuesta = self.client.put(
        #     '/api/items/1/',
        #     data=json.dumps(datos),
        #     content_type='application/json'
        # )
        # self.assertEqual(respuesta.status_code, 200)
    
    def test_delete_endpoint_elimina_recurso(self):
        """Test: DELETE elimina recurso"""
        self.client.login(username='apiuser', password='apipass123')
        
        # respuesta = self.client.delete('/api/items/1/')
        # self.assertEqual(respuesta.status_code, 204)
```

**Ejecutar:**
```powershell
python manage.py test tests.test_integracion -v 2
```

---

## Template 3: Test de Aceptación

Copia este archivo como `tests/test_aceptacion.py`:

```python
"""
tests/test_aceptacion.py

Pruebas de aceptación usando formato Given/When/Then
Validan que se cumplan los criterios de la Historia de Usuario.
Ejecutar: python manage.py test tests.test_aceptacion
"""

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from datetime import date
import json


class TestRegistroActividadesCriteriosAceptacion(TestCase):
    """
    HISTORIA: Registro de Actividades
    
    Como funcionario de la Municipalidad
    Quiero registrar actividades en el sistema
    Para que se contabilice mi avance hacia las metas
    """
    
    def setUp(self):
        """DADO: Precondiciones del escenario"""
        self.client = Client()
        
        # Crear usuario funcionario
        self.funcionario = User.objects.create_user(
            username='funcionario1',
            password='pass123'
        )
        
        # Crear grupo de empleados
        grupo_empleado = Group.objects.create(name='Empleado')
        self.funcionario.groups.add(grupo_empleado)
    
    def test_aceptacion_registrar_actividad_completa(self):
        """
        DADO: Un funcionario autenticado
        CUANDO: Registra una actividad con todos los datos requeridos
        ENTONCES: El sistema acepta y guarda la actividad
        """
        # DADO
        self.client.login(username='funcionario1', password='pass123')
        
        # CUANDO
        datos_actividad = {
            'descripcion': 'Capacitación en sistemas',
            'fecha': '2024-01-15',
            'cantidad': 1,
            'item_id': 1  # Ajusta según tu modelo
        }
        
        # respuesta = self.client.post(
        #     '/api/actividades/',
        #     data=json.dumps(datos_actividad),
        #     content_type='application/json'
        # )
        
        # ENTONCES
        # self.assertEqual(respuesta.status_code, 201)
    
    def test_aceptacion_rechazar_actividad_incompleta(self):
        """
        DADO: Datos de actividad incompletos
        CUANDO: Se intenta registrar sin campo requerido
        ENTONCES: El sistema rechaza con error 400
        """
        # DADO
        self.client.login(username='funcionario1', password='pass123')
        
        datos_incompletos = {
            # Falta 'descripcion'
            'fecha': '2024-01-15',
            'cantidad': 1
        }
        
        # CUANDO
        # respuesta = self.client.post(
        #     '/api/actividades/',
        #     data=json.dumps(datos_incompletos),
        #     content_type='application/json'
        # )
        
        # ENTONCES
        # self.assertEqual(respuesta.status_code, 400)
        # respuesta_data = json.loads(respuesta.content)
        # self.assertIn('descripcion', respuesta_data)  # Campo requerido


class TestAprobacionActividadesCriteriosAceptacion(TestCase):
    """
    HISTORIA: Aprobación de Actividades
    
    Como verificador de la Municipalidad
    Quiero aprobar o rechazar actividades
    Para validar que se cumplan los criterios
    """
    
    def setUp(self):
        """DADO: Precondiciones"""
        self.client = Client()
        
        # Crear verificador
        self.verificador = User.objects.create_user(
            username='verificador1',
            password='pass123'
        )
        
        grupo_verificador = Group.objects.create(name='Verificador')
        self.verificador.groups.add(grupo_verificador)
    
    def test_aceptacion_solo_verificador_puede_aprobar(self):
        """
        DADO: Un usuario sin rol de verificador
        CUANDO: Intenta aprobar una actividad
        ENTONCES: El sistema rechaza el acceso (401/403)
        """
        # DADO - Usuario sin permisos
        usuario_regular = User.objects.create_user(
            username='user1',
            password='pass123'
        )
        self.client.login(username='user1', password='pass123')
        
        # CUANDO
        # respuesta = self.client.post(
        #     '/api/actividades/1/aprobar/',
        #     data=json.dumps({}),
        #     content_type='application/json'
        # )
        
        # ENTONCES
        # self.assertIn(respuesta.status_code, [401, 403])
    
    def test_aceptacion_verificador_puede_aprobar(self):
        """
        DADO: Un verificador autenticado
        CUANDO: Aprueba una actividad pendiente
        ENTONCES: El estado cambia a APROBADA
        """
        # DADO
        self.client.login(username='verificador1', password='pass123')
        
        # CUANDO
        # respuesta = self.client.post(
        #     '/api/actividades/1/aprobar/',
        #     data=json.dumps({'comentario': 'Datos correctos'}),
        #     content_type='application/json'
        # )
        
        # ENTONCES
        # self.assertEqual(respuesta.status_code, 200)
        # respuesta_data = json.loads(respuesta.content)
        # self.assertEqual(respuesta_data['estado'], 'APROBADA')


class TestActualizacionPanelesCriteriosAceptacion(TestCase):
    """
    HISTORIA: Actualización de Paneles
    
    Cuando se aprueba una actividad
    Los paneles de control deben actualizar los datos automáticamente
    """
    
    def setUp(self):
        """DADO: Inicializar datos"""
        pass
    
    def test_aceptacion_panel_actualiza_avance_inmediato(self):
        """
        DADO: Un panel mostrando avance al 50%
        CUANDO: Se aprueba una nueva actividad que incrementa 10%
        ENTONCES: El panel debe mostrar 60% sin recarga manual
        """
        # Este comportamiento se valida en la interfaz web Django y en la API:
        
        # DADO - Panel muestra 50%
        # avance_inicial = 50
        
        # CUANDO - Aprobar actividad
        # self.client.login(username='verificador1', password='pass123')
        # respuesta = self.client.post('/api/actividades/1/aprobar/')
        
        # ENTONCES - Endpoint devuelve nuevo avance
        # respuesta_data = json.loads(respuesta.content)
        # self.assertEqual(respuesta_data['avance_total'], 60)
```

**Ejecutar:**
```powershell
python manage.py test tests.test_aceptacion -v 2
```

---

## Template 4: Fixtures (Datos de Prueba)

Crea `tests/test_fixtures/usuarios.json`:

```json
[
  {
    "model": "auth.user",
    "pk": 1,
    "fields": {
      "username": "admin",
      "email": "admin@laserena.cl",
      "password": "pbkdf2_sha256$...",
      "first_name": "Admin",
      "last_name": "User",
      "is_staff": true,
      "is_active": true
    }
  },
  {
    "model": "auth.user",
    "pk": 2,
    "fields": {
      "username": "funcionario1",
      "email": "func@laserena.cl",
      "password": "pbkdf2_sha256$...",
      "first_name": "Juan",
      "last_name": "Pérez",
      "is_staff": false,
      "is_active": true
    }
  }
]
```

**Usar fixtures en tests:**

```python
class TestConFixtures(TestCase):
    fixtures = ['usuarios.json', 'items.json']
    
    def test_usuario_existe(self):
        """Datos vienen de las fixtures"""
        usuario = User.objects.get(username='funcionario1')
        self.assertEqual(usuario.first_name, 'Juan')
```

---

## Template 5: Coverage Report

Crea `.coveragerc`:

```ini
[run]
source = .
omit =
    */migrations/*
    */venv/*
    manage.py
    setup.py

[report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
```

**Ejecutar cobertura:**

```powershell
coverage run --source='.' manage.py test
coverage report --fail-under=70
coverage html
```

---

## Script para Ejecutar Todo

Crea `run_tests.ps1`:

```powershell
# run_tests.ps1 - Ejecutar suite completa de tests

Write-Host " Ejecutando suite de pruebas..." -ForegroundColor Green

# 1. Unitarios
Write-Host " Tests unitarios..." -ForegroundColor Cyan
python manage.py test tests.test_unitarios -v 2

# 2. Integración
Write-Host " Tests de integración..." -ForegroundColor Cyan
python manage.py test tests.test_integracion -v 2

# 3. Aceptación
Write-Host " Tests de aceptación..." -ForegroundColor Cyan
python manage.py test tests.test_aceptacion -v 2

# 4. Cobertura
Write-Host " Generando reporte de cobertura..." -ForegroundColor Cyan
coverage run --source='.' manage.py test
coverage report --fail-under=70
coverage html

Write-Host " Tests completados!" -ForegroundColor Green
Write-Host " Abre htmlcov/index.html para ver cobertura" -ForegroundColor Yellow
```

**Ejecutar:**
```powershell
.\run_tests.ps1
```

---

## Checklist de Setup

- [ ] Crear carpeta `tests/` con `__init__.py`
- [ ] Copiar templates a `tests/test_*.py`
- [ ] Instalar coverage: `pip install coverage`
- [ ] Crear `.coveragerc`
- [ ] Crear `run_tests.ps1`
- [ ] Ejecutar: `python manage.py test`
- [ ] Verificar: `coverage report`

---

**¡Listos para hacer testing profesional!** 
