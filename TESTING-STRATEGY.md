#  Estrategia de Pruebas - Proyecto SGR La Serena

Este documento establece la **estrategia mínima obligatoria de pruebas** que todo código debe pasar antes de ser mergeado a `main`.

---

## Resumen Ejecutivo

Cada historia de usuario debe validarse a través de **3 niveles de pruebas**:

1. **Pruebas Unitarias** - Funciones y reglas de negocio aisladas
2. **Pruebas de Integración** - Base de datos, migraciones, APIs
3. **Pruebas de Aceptación** - Criterios de la HU (Given/When/Then)

Además, se debe auditar:
-  Gestión de secretos (sin credenciales en git)
-  Control de acceso por servidor
-  Validación de entradas
-  Cobertura mínima de 70% en funciones críticas

---

## Nivel 1: Pruebas Unitarias

### Definición
Verifican que las funciones individuales, cálculos y reglas de negocio funcionan correctamente de forma **aislada**, sin dependencias externas.

### Ejemplos del Proyecto SGR

#### Ejemplo 1: Cálculo de Porcentaje de Cumplimiento

**Función a probar:**
```python
# utils/calculators.py
def calcular_porcentaje_cumplimiento(avance, meta):
    """
    Calcula el porcentaje de cumplimiento de una meta.
    
    Args:
        avance (float): Valor actual alcanzado
        meta (float): Valor meta a alcanzar
        
    Returns:
        float: Porcentaje de cumplimiento (0-100)
    """
    if meta == 0:
        return 0.0
    porcentaje = (avance / meta) * 100
    return min(porcentaje, 100.0)  # Máximo 100%
```

**Prueba Unitaria:**
```python
# tests/test_calculators.py
from django.test import TestCase
from utils.calculators import calcular_porcentaje_cumplimiento

class TestCalcularPorcentajeCumplimiento(TestCase):
    
    def test_cumplimiento_50_porciento(self):
        """Cuando avance es 50 y meta es 100, devuelve 50%"""
        resultado = calcular_porcentaje_cumplimiento(50, 100)
        self.assertEqual(resultado, 50.0)
    
    def test_cumplimiento_completo(self):
        """Cuando avance >= meta, devuelve 100%"""
        resultado = calcular_porcentaje_cumplimiento(150, 100)
        self.assertEqual(resultado, 100.0)
    
    def test_meta_cero(self):
        """Cuando meta es 0, devuelve 0% sin error"""
        resultado = calcular_porcentaje_cumplimiento(50, 0)
        self.assertEqual(resultado, 0.0)
    
    def test_avance_cero(self):
        """Cuando avance es 0, devuelve 0%"""
        resultado = calcular_porcentaje_cumplimiento(0, 100)
        self.assertEqual(resultado, 0.0)
```

**Ejecutar:**
```powershell
python manage.py test tests.test_calculators.TestCalcularPorcentajeCumplimiento -v 2
```

#### Ejemplo 2: Lógica de Semáforos

**Función a probar:**
```python
# utils/semaforos.py
def obtener_color_semaforo(porcentaje):
    """
    Devuelve el color del semáforo según cumplimiento.
    
    - Verde: >= 90%
    - Ámbar: >= 60% y < 90%
    - Rojo: < 60%
    """
    if porcentaje >= 90:
        return 'VERDE'
    elif porcentaje >= 60:
        return 'AMBAR'
    else:
        return 'ROJO'
```

**Prueba Unitaria:**
```python
# tests/test_semaforos.py
from django.test import TestCase
from utils.semaforos import obtener_color_semaforo

class TestObtenerColorSemaforo(TestCase):
    
    def test_semaforo_verde_90_porciento(self):
        """Cumplimiento >= 90% es VERDE"""
        self.assertEqual(obtener_color_semaforo(90), 'VERDE')
        self.assertEqual(obtener_color_semaforo(100), 'VERDE')
    
    def test_semaforo_ambar_60_a_89_porciento(self):
        """Cumplimiento 60-89% es ÁMBAR"""
        self.assertEqual(obtener_color_semaforo(60), 'AMBAR')
        self.assertEqual(obtener_color_semaforo(75), 'AMBAR')
        self.assertEqual(obtener_color_semaforo(89), 'AMBAR')
    
    def test_semaforo_rojo_menos_60_porciento(self):
        """Cumplimiento < 60% es ROJO"""
        self.assertEqual(obtener_color_semaforo(0), 'ROJO')
        self.assertEqual(obtener_color_semaforo(59), 'ROJO')
```

#### Ejemplo 3: Validación de RUT

**Función a probar:**
```python
# utils/validators.py
def es_rut_valido(rut):
    """
    Valida el formato de RUT chileno: XX.XXX.XXX-X
    
    Ejemplo válido: 12.345.678-9
    """
    import re
    patron = r'^\d{1,2}\.\d{3}\.\d{3}-[0-9K]$'
    return bool(re.match(patron, rut))
```

**Prueba Unitaria:**
```python
# tests/test_validators.py
from django.test import TestCase
from utils.validators import es_rut_valido

class TestEsRutValido(TestCase):
    
    def test_rut_valido_formato_correcto(self):
        """RUT con formato correcto es válido"""
        self.assertTrue(es_rut_valido('12.345.678-9'))
        self.assertTrue(es_rut_valido('1.234.567-K'))
    
    def test_rut_invalido_sin_puntos(self):
        """RUT sin puntos es inválido"""
        self.assertFalse(es_rut_valido('12345678-9'))
    
    def test_rut_invalido_sin_guion(self):
        """RUT sin guión es inválido"""
        self.assertFalse(es_rut_valido('12.345.678'))
    
    def test_rut_invalido_digito_verificador(self):
        """RUT con dígito verificador inválido es inválido"""
        self.assertFalse(es_rut_valido('12.345.678-@'))
```

### Checklist de Pruebas Unitarias

Antes de subir tu PR, verifica:

- [ ] Todas las funciones críticas tienen al menos 3 tests
- [ ] Se prueban casos normales (happy path)
- [ ] Se prueban casos límite (edge cases)
- [ ] Se prueban casos de error (excepciones)
- [ ] Cobertura mínima: 70% en funciones críticas
- [ ] Tests ejecutados localmente: `python manage.py test`

**Ejecutar cobertura:**
```powershell
pip install coverage
coverage run --source='.' manage.py test
coverage report
coverage html  # Abre htmlcov/index.html en el navegador
```

---

## Nivel 2: Pruebas de Integración

### Definición
Verifican que el **sistema completo funciona integrado**: base de datos, migraciones, APIs, conectores externos.

### Ejemplos del Proyecto SGR

#### Ejemplo 1: Crear Empleado en BD

**Test de Integración:**
```python
# tests/test_empleado_integration.py
from django.test import TestCase
from empleados.models import Empleado
from datetime import date

class TestCrearEmpleadoIntegracion(TestCase):
    
    def test_crear_empleado_completo(self):
        """Crear empleado y verificar que se guarda en BD"""
        empleado = Empleado.objects.create(
            nombre='Juan',
            apellido='Pérez',
            rut='12.345.678-9',
            email='juan.perez@laserena.cl',
            rol='empleado',
            fecha_contratacion=date(2024, 1, 15)
        )
        
        # Verificar que se creó
        self.assertIsNotNone(empleado.id)
        
        # Recuperar desde BD
        empleado_recuperado = Empleado.objects.get(id=empleado.id)
        self.assertEqual(empleado_recuperado.nombre, 'Juan')
        self.assertEqual(empleado_recuperado.rut, '12.345.678-9')
    
    def test_no_permitir_rut_duplicado(self):
        """No se puede crear dos empleados con el mismo RUT"""
        Empleado.objects.create(
            nombre='Juan',
            apellido='Pérez',
            rut='12.345.678-9',
            email='juan@laserena.cl',
            rol='empleado',
            fecha_contratacion=date(2024, 1, 15)
        )
        
        # Intentar crear otro con el mismo RUT debe fallar
        with self.assertRaises(Exception):  # IntegrityError
            Empleado.objects.create(
                nombre='Carlos',
                apellido='López',
                rut='12.345.678-9',  # RUT duplicado
                email='carlos@laserena.cl',
                rol='empleado',
                fecha_contratacion=date(2024, 1, 15)
            )
```

#### Ejemplo 2: Migraciones de Base de Datos

**Test de Integración:**
```python
# tests/test_migraciones.py
from django.test import TestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

class TestMigraciones(TestCase):
    
    def test_migraciones_aplican_sin_errores(self):
        """Las migraciones deben ejecutarse sin errores"""
        executor = MigrationExecutor(connection)
        
        # Obtener cambios pendientes
        cambios_pendientes = executor.migration_plan(
            executor.loader.graph.leaf_nodes()
        )
        
        # No debe haber migraciones sin aplicar
        self.assertEqual(len(cambios_pendientes), 0)
```

**Ejecutar:**
```powershell
python manage.py test tests.test_migraciones
```

#### Ejemplo 3: API REST Endpoint

**Test de Integración:**
```python
# tests/test_api_empleados.py
from django.test import TestCase, Client
from empleados.models import Empleado
from datetime import date
import json

class TestAPIEmpleados(TestCase):
    
    def setUp(self):
        """Crear datos de prueba"""
        self.client = Client()
        self.empleado = Empleado.objects.create(
            nombre='Juan',
            apellido='Pérez',
            rut='12.345.678-9',
            email='juan@laserena.cl',
            rol='empleado',
            fecha_contratacion=date(2024, 1, 15)
        )
    
    def test_listar_empleados_api(self):
        """GET /api/empleados/ retorna lista de empleados"""
        respuesta = self.client.get('/api/empleados/')
        
        self.assertEqual(respuesta.status_code, 200)
        datos = json.loads(respuesta.content)
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]['nombre'], 'Juan')
    
    def test_crear_empleado_api(self):
        """POST /api/empleados/ crea nuevo empleado"""
        datos = {
            'nombre': 'Carlos',
            'apellido': 'López',
            'rut': '11.111.111-1',
            'email': 'carlos@laserena.cl',
            'rol': 'empleado',
            'fecha_contratacion': '2024-02-01'
        }
        
        respuesta = self.client.post(
            '/api/empleados/',
            data=json.dumps(datos),
            content_type='application/json'
        )
        
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(Empleado.objects.count(), 2)
    
    def test_obtener_empleado_por_id(self):
        """GET /api/empleados/{id}/ retorna empleado específico"""
        respuesta = self.client.get(f'/api/empleados/{self.empleado.id}/')
        
        self.assertEqual(respuesta.status_code, 200)
        datos = json.loads(respuesta.content)
        self.assertEqual(datos['rut'], '12.345.678-9')
```

### Checklist de Pruebas de Integración

- [ ] Se prueban las migraciones de BD
- [ ] Se prueban endpoints de API (GET, POST, PUT, DELETE)
- [ ] Se valida código de estado HTTP correcto
- [ ] Se prueba con datos reales de BD
- [ ] Se prueban restricciones de integridad (UNIQUE, FK)
- [ ] Se prueban conexiones a servicios externos (si aplica)

---

## Nivel 3: Pruebas de Aceptación

### Definición
Validan que la historia de usuario **cumple con los criterios descritos en Jira** usando el formato **Given / When / Then** (Dado / Cuando / Entonces).

### Ejemplo: Registro de Actividades

**Historia de Usuario (MLS-5):**
```
Como funcionario de la Municipalidad
Quiero registrar actividades en el sistema
Para que se contabilice mi avance hacia las metas

Criterios de Aceptación:
- El sistema solo permite registrar actividades con datos completos
- El avance se incrementa solo cuando el verificador aprueba
- Los paneles actualizan en tiempo real
- El semáforo cambia de color según el cumplimiento
```

**Prueba de Aceptación:**
```python
# tests/test_aceptacion_registro_actividades.py
from django.test import TestCase, Client
from django.contrib.auth.models import User
from actividades.models import Actividad, Evidencia
from items.models import Item
from datetime import date
import json

class TestRegistroActividadesAceptacion(TestCase):
    """
    HISTORIA: MLS-5 - Registro de Actividades
    
    Dado un funcionario autenticado con rol válido
    Cuando registra una actividad completa
    Entonces el sistema acepta los datos y el verificador puede aprobar
    """
    
    def setUp(self):
        """Preparar datos: usuario, item, meta"""
        self.client = Client()
        
        # Crear usuario funcionario
        self.funcionario = User.objects.create_user(
            username='funcionario1',
            password='pass123',
            email='func@laserena.cl'
        )
        
        # Crear item con meta
        self.item = Item.objects.create(
            nombre='Capacitaciones Realizadas',
            descripcion='Número de capacitaciones',
            meta=10,
            unidad='Eventos'
        )
    
    def test_aceptacion_registro_actividad_completa(self):
        """
        DADO: Funcionario autenticado
        CUANDO: Registra actividad con datos completos
        ENTONCES: Sistema acepta y guarda la actividad
        """
        # Autenticar
        self.client.login(username='funcionario1', password='pass123')
        
        # Cuando: Registrar actividad
        datos_actividad = {
            'item': self.item.id,
            'descripcion': 'Capacitación en sistemas municipales',
            'fecha': '2024-01-15',
            'cantidad': 1,
        }
        
        respuesta = self.client.post(
            '/api/actividades/',
            data=json.dumps(datos_actividad),
            content_type='application/json'
        )
        
        # Entonces: Status 201 (creado)
        self.assertEqual(respuesta.status_code, 201)
        
        # Entonces: Actividad está pendiente de aprobación
        actividad = Actividad.objects.latest('id')
        self.assertEqual(actividad.estado, 'PENDIENTE')
    
    def test_aceptacion_verificador_aprueba_evidencia(self):
        """
        DADO: Una actividad pendiente con evidencia
        CUANDO: El verificador aprueba
        ENTONCES: El avance del item aumenta UNA SOLA VEZ
        """
        # Crear actividad
        actividad = Actividad.objects.create(
            item=self.item,
            funcionario=self.funcionario,
            descripcion='Capacitación',
            fecha=date(2024, 1, 15),
            cantidad=1,
            estado='PENDIENTE'
        )
        
        avance_antes = self.item.obtener_avance()
        
        # Cuando: Verificador aprueba
        actividad.estado = 'APROBADA'
        actividad.save()
        
        # Recargar item
        self.item.refresh_from_db()
        avance_despues = self.item.obtener_avance()
        
        # Entonces: El avance aumentó en 1
        self.assertEqual(avance_despues, avance_antes + 1)
    
    def test_aceptacion_semaforo_actualiza_automaticamente(self):
        """
        DADO: Un item con semáforo rojo (bajo cumplimiento)
        CUANDO: Se registran actividades que llegan al 90%
        ENTONCES: El semáforo cambia a verde automáticamente
        """
        # Supongamos meta=10, necesitamos 9 para 90%
        for i in range(9):
            Actividad.objects.create(
                item=self.item,
                funcionario=self.funcionario,
                descripcion=f'Actividad {i}',
                fecha=date(2024, 1, 15),
                cantidad=1,
                estado='APROBADA'
            )
        
        # Cuando: Consultamos el color del semáforo
        color_semaforo = self.item.obtener_color_semaforo()
        
        # Entonces: Es VERDE
        self.assertEqual(color_semaforo, 'VERDE')
```

### Checklist de Pruebas de Aceptación

- [ ] Se validan todos los criterios de aceptación de la HU
- [ ] Se usan formatos Given/When/Then
- [ ] Se prueban flujos completos (usuario final)
- [ ] Se prueban casos de éxito y error
- [ ] Se documentan en la descripción del PR

---

## Auditoría de Seguridad y Calidad

### Requisito 1: Gestión de Secretos

** Lo Correcto:**
```python
# settings.py
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
DATABASE_PASSWORD = os.getenv('DB_PASSWORD')
API_KEY_GOOGLE = os.getenv('GOOGLE_DRIVE_API_KEY')
```

```env
# .env (NO subir a GitHub)
DJANGO_SECRET_KEY=tu-clave-secreta-123
DB_PASSWORD=tu-password-bd
GOOGLE_DRIVE_API_KEY=tu-api-key
```

```gitignore
# .gitignore
.env
*.key
*.pem
secrets/
```

** Lo Incorrecto:**
```python
# ¡NUNCA!
SECRET_KEY = 'abc123xyz456'  # Expuesto en GitHub
DATABASE_PASSWORD = 'mi-password-123'  # Visible para todos
```

### Requisito 2: Control de Acceso por Servidor

** Validación en Backend (Django):**
```python
# views.py
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework import status

class EsEmpleadoMunicipal(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.groups.filter(name='Empleado').exists()

class EsVerificador(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.groups.filter(name='Verificador').exists()

class ActividadViewSet(viewsets.ModelViewSet):
    permission_classes = [EsEmpleadoMunicipal]
    
    def get_queryset(self):
        # Cada usuario solo ve sus propias actividades
        return Actividad.objects.filter(funcionario=self.request.user)
    
    @action(detail=True, methods=['post'], permission_classes=[EsVerificador])
    def aprobar(self, request, pk=None):
        """Solo verificadores pueden aprobar"""
        actividad = self.get_object()
        actividad.estado = 'APROBADA'
        actividad.save()
        return Response({'status': 'Aprobada'})
```

** No Confiar en el Frontend:**
```javascript
// ¡NUNCA hacer esto!
if (usuario.rol === 'admin') {
    // Mostrar botón de eliminar
}
// Un usuario malicioso puede modificar esto en DevTools
```

### Requisito 3: Validación de Entradas

** Validación en Models:**
```python
# models.py
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

class Actividad(models.Model):
    ESTADOS = [
        ('PENDIENTE', 'Pendiente de Aprobación'),
        ('APROBADA', 'Aprobada'),
        ('RECHAZADA', 'Rechazada'),
    ]
    
    cantidad = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(1000)]
    )
    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='PENDIENTE'
    )
    descripcion = models.CharField(
        max_length=500,
        blank=False  # Obligatorio
    )
    
    def clean(self):
        if self.fecha > date.today():
            raise ValidationError("La fecha no puede ser en el futuro")
```

** Validación en Serializer:**
```python
# serializers.py
from rest_framework import serializers

class ActividadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Actividad
        fields = ['id', 'item', 'cantidad', 'descripcion', 'fecha']
    
    def validate_cantidad(self, value):
        if value <= 0:
            raise serializers.ValidationError("La cantidad debe ser mayor a 0")
        return value
    
    def validate_fecha(self, value):
        if value > date.today():
            raise serializers.ValidationError("La fecha no puede ser en el futuro")
        return value
```

### Checklist de Seguridad

- [ ] No hay `.env` en git (verificar `.gitignore`)
- [ ] No hay credenciales en código
- [ ] Permisos validados en backend
- [ ] Inputs validados (longitud, tipo, formato)
- [ ] Consultas protegidas contra inyección SQL
- [ ] Contraseñas hasheadas (Django lo hace automáticamente)
- [ ] HTTPS configurado en producción
- [ ] CORS configurado correctamente

---

## Resumen del Checklist Completo de QA

**Antes de crear el PR:**
- [ ] Pruebas unitarias ejecutadas: `python manage.py test` (70%+ cobertura)
- [ ] Pruebas de integración ejecutadas
- [ ] Código local verificado: `python manage.py runserver`
- [ ] `requirements.txt` actualizado

**En el PR:**
- [ ] Título comienza con ID de Jira
- [ ] Descripción incluye cambios realizados
- [ ] Se adjuntan resultados de tests
- [ ] Se documenta cualquier variable de entorno nueva

**En Revisión de Código (Tu rol):**
- [ ]  No hay credenciales expuestas
- [ ]  Permisos validados en backend
- [ ]  Inputs validados
- [ ]  Cobertura de tests >= 70%
- [ ]  `requirements.txt` actualizado
- [ ]  Migraciones creadas correctamente
- [ ]  Criterios de aceptación cumplidos

**Antes de Merge:**
- [ ] PR aprobado 
- [ ] Tests pasan 
- [ ] Seguridad auditada 
- [ ] Matriz de trazabilidad actualizada 

---

## Ejecutar Todos los Tests

```powershell
# Ejecutar todos los tests con cobertura
coverage run --source='.' manage.py test
coverage report
coverage html

# Ver reporte en navegador
start htmlcov/index.html
```

---

## Preguntas Frecuentes

**P: ¿Cuál es la cobertura mínima requerida?**
R: 70% en funciones críticas (cálculos, validaciones, APIs).

**P: ¿Debo escribir tests antes o después del código?**
R: Idealmente antes (TDD), pero después está bien si cumples 70%.

**P: ¿Qué si una librería externa no se puede testear fácilmente?**
R: Mockéala:
```python
from unittest.mock import patch

@patch('requests.get')
def test_api_externa(self, mock_get):
    mock_get.return_value.status_code = 200
    # Tu test aquí
```

**P: ¿Es obligatorio Selenium para pruebas UI?**
R: No para MVP. Enfócate en backend (Django tests son suficientes).

---

**¡La calidad es responsabilidad de todos!** 
