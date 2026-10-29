#  Matriz de Trazabilidad - Rastreabilidad de Requisitos

Este documento establece cómo mantener la **trazabilidad** entre Historias de Usuario, Requisitos Funcionales, Commits y Pruebas en el proyecto SGR.

---

## ¿Qué es la Matriz de Trazabilidad?

La Matriz de Trazabilidad es un documento que **vincula**:

```
Requisito Funcional (RF-XXX)
    ↓
Historia de Usuario (HU-XXX / MLS-XX)
    ↓
Commits en Git (MLS-XX: descripción)
    ↓
Pruebas Automatizadas (Tests unitarios, integración, aceptación)
    ↓
Evidencia de Aprobación (PR approved, merge a main)
```

**Beneficios:**
-  Auditoría académica y profesional
-  Rastrear qué código implementa cuál requisito
-  Verificar que todo está testeado
-  Facilita el debugging (saber por qué existe una línea de código)

---

## Matriz de Trazabilidad Completa

### Formato Recomendado

Puedes mantenerla en:
1. **Un documento de Word/Excel** (más fácil para audit)
2. **En Jira** (integrado en el flujo)
3. **En GitHub Wiki** (versión controlada)

### Template en Markdown

```markdown
# Matriz de Trazabilidad - Proyecto SGR La Serena

## Período: Enero 2024

| RF | HU | Descripción | Commits | Tests | PR # | Estado |
|----|----|-------------|---------|-------|------|--------|
| RF-001 | MLS-1 | Autenticación de usuarios | MLS-1: Implementar login, MLS-1: Add JWT | test_auth.py (5 tests) | #3 |  Aprobado |
| RF-002 | MLS-2 | Gestión de roles | MLS-2: Model Role, MLS-2: API Role CRUD | test_roles.py (8 tests) | #5 |  Aprobado |
| RF-003 | MLS-3 | CRUD de Empleados | MLS-3: Model Employee, MLS-3: Serializer | test_empleados.py (12 tests) | #7 |  Aprobado |
```

---

## Ejemplo Detallado: Requisito RF-003

### Requisito Funcional

```
ID: RF-003
Nombre: CRUD de Empleados
Descripción: El sistema permite crear, leer, actualizar y eliminar registros de empleados 
            de la Municipalidad con validación de RUT único.

Criterios:
- Validar formato RUT (XX.XXX.XXX-X)
- RUT debe ser único en el sistema
- Campos requeridos: nombre, apellido, RUT, email, rol
- Solo administradores pueden eliminar empleados
- Auditar cambios (quién modificó y cuándo)

Prioridad: Alta
Módulo: Gestión de Recursos Humanos
```

### Historia de Usuario Jira

```
TICKET: MLS-3
ÉPICA: Gestión de Empleados
TÍTULO: Implementar CRUD completo de Empleados

Como administrador del sistema
Quiero poder gestionar empleados (crear, editar, ver, eliminar)
Para que se mantengan actualizados los datos del personal

Criterios de Aceptación:
- [x] Crear empleado con validación de RUT
- [x] Listar todos los empleados
- [x] Editar datos de empleado
- [x] Eliminar empleado (solo admin)
- [x] No permitir RUT duplicado
- [x] Auditar cambios en la BD

Tamaño: 13 puntos
Sprint: Sprint 1
Assignee: Matías
```

### Commits Vinculados

```
commit abc1234d5
Author: Matías <matias@laserena.cl>
Date: 2024-01-15

MLS-3: Crear modelo de Empleados con validación de RUT

- Agregar modelo Employee a empleados/models.py
- Validar formato RUT con expresión regular
- Agregar restricción UNIQUE en RUT
- Agregar campos: nombre, apellido, email, rol, fecha_contratación

commit def5678e9
Author: Matías
Date: 2024-01-16

MLS-3: Implementar serializador y viewset REST para Empleados

- Crear EmpleadoSerializer en empleados/serializers.py
- Crear EmpleadoViewSet con CRUD completo
- Agregar filtros y búsqueda por RUT
- Agregar acción personalizada: /empleados/{id}/cambiar-rol/

commit ghi9012k3
Author: Matías
Date: 2024-01-17

MLS-3: Agregar tests unitarios e integración para Empleados

- Tests unitarios para validación de RUT
- Tests de integración con BD
- Tests de API endpoints
- Cobertura: 85%

commit jkl3456m7
Author: Matías
Date: 2024-01-17

MLS-3: Agregar auditoría de cambios en Empleados

- Crear modelo EmpleadoLog para auditoría
- Registrar quién cambió qué y cuándo
- Agregar endpoint GET /empleados/{id}/historial/
```

### Pruebas Automatizadas

```python
# tests/test_empleados.py

class TestEmpleadoUnitarios(TestCase):
    """Tests unitarios para modelo Empleado"""
    
    def test_validar_rut_formato_correcto(self):
        """Test RF-003.1: Validar RUT formato XX.XXX.XXX-X"""
        #  PASA
    
    def test_rechazar_rut_incorrecto(self):
        """Test RF-003.1: Rechazar RUT con formato incorrecto"""
        #  PASA
    
    def test_campo_nombre_requerido(self):
        """Test RF-003: Campo nombre es obligatorio"""
        #  PASA

class TestEmpleadoIntegracion(TestCase):
    """Tests de integración con BD"""
    
    def test_crear_empleado_en_bd(self):
        """Test RF-003.2: Crear empleado persiste en BD"""
        #  PASA
    
    def test_rut_unico_en_bd(self):
        """Test RF-003.3: No permitir RUT duplicado"""
        #  PASA
    
    def test_api_crear_empleado(self):
        """Test RF-003.2: API POST /empleados/ crea empleado"""
        #  PASA (201 Created)
    
    def test_api_listar_empleados(self):
        """Test RF-003.2: API GET /empleados/ lista empleados"""
        #  PASA (200 OK)
    
    def test_api_actualizar_empleado(self):
        """Test RF-003.2: API PUT /empleados/{id}/ actualiza"""
        #  PASA (200 OK)
    
    def test_api_eliminar_solo_admin(self):
        """Test RF-003.4: Solo admin puede eliminar"""
        #  PASA (403 Forbidden para no-admin)

class TestEmpleadoAceptacion(TestCase):
    """Tests de aceptación según HU-MLS-3"""
    
    def test_aceptacion_crear_empleado_valido(self):
        """AC: Crear empleado con datos válidos"""
        #  PASA
    
    def test_aceptacion_rechazar_empleado_invalido(self):
        """AC: Rechazar datos incompletos"""
        #  PASA
    
    def test_aceptacion_auditar_cambios(self):
        """AC: Registrar cambios en auditoría"""
        #  PASA
```

### Pull Request en GitHub

```
Title: MLS-3: Implementar CRUD completo de Empleados

Description:
## Descripción
Implementa la gestión completa de empleados incluyendo validación de RUT, 
auditoría de cambios y control de acceso.

## Cambios Realizados
-  Modelo Employee con validación de RUT
-  Serializador y ViewSet REST
-  Tests unitarios (5 tests, 85% cobertura)
-  Tests de integración (8 tests)
-  Tests de aceptación (3 tests)
-  Auditoría de cambios
-  Documentación de API

## Vinculación
- Jira: MLS-3
- Requisito: RF-003
- Commits: abc1234, def5678, ghi9012, jkl3456

## Tests
```
python manage.py test tests.test_empleados
======================================================================
Ran 16 tests in 0.234s

OK

Coverage: 85%
```

## Revisión de Seguridad
-  No hay credenciales expuestas
-  Permisos validados en backend (solo admin puede eliminar)
-  Inputs validados (RUT, email)
-  Queries protegidas contra SQL injection

Status: Ready for Review
```

### Aprobación y Merge

```
PR #7 Approved by DevLead 
Merged into main by DevLead
Commit merged: abc1234...jkl3456

Jira MLS-3 automatically updated:
- Status: Done 
- Linked PR: #7
- Commits: 4

Timeline:
- 2024-01-15: Started
- 2024-01-17: PR created
- 2024-01-17: Code review passed
- 2024-01-17: Merged to main
```

---

## Matriz de Ejemplo Completa (Simplificada)

| RF | HU | Descripción | Tests | Cobertura | PR | Commits | Estado | Aprobado |
|----|----|-----------|----|-----------|----|----|--------|----------|
| RF-001 | MLS-1 | Autenticación | test_auth.py (5) | 80% | #3 | 3 |  Merged | Dev 1 |
| RF-002 | MLS-2 | Gestión de Roles | test_roles.py (8) | 75% | #5 | 4 |  Merged | Dev 1 |
| RF-003 | MLS-3 | CRUD Empleados | test_empleados.py (16) | 85% | #7 | 4 |  Merged | Dev 1 |
| RF-004 | MLS-4 | Registro de Actividades | test_actividades.py (12) | 78% | #9 | 5 |  En Revisión | Pendiente |
| RF-005 | MLS-5 | Reportes | test_reportes.py (8) | 70% | #11 | 3 |  En Desarrollo | No iniciado |

---

## ️ Cómo Mantener la Matriz

### Opción 1: Google Sheets (Fácil para el equipo)

1. Crea una hoja de cálculo compartida
2. Columnas: RF | HU | Descripción | Tests | Cobertura | PR # | Commits | Estado | Aprobador
3. Actualizar después de cada PR merged
4. Adjunta el link en el README.md

### Opción 2: En Jira Directamente

En la descripción de cada Épica o Ticket, agregar:

```
## Trazabilidad
- **Requisito:** RF-003
- **Commits:** MLS-3 (4 commits)
- **Tests:** 16 tests, 85% cobertura
- **PR:** #7
- **Aprobador:** Dev 1
- **Fecha Merge:** 2024-01-17
```

### Opción 3: GitHub Wiki (Control de Versión)

Crear página en GitHub Wiki: "Traceability Matrix"
- Fácil de actualizar
- Versionado automáticamente
- Accesible desde GitHub

---

## Template para Cada PR

Cuando crees un PR, **siempre incluye** esta sección:

```markdown
## Trazabilidad

| Elemento | Valor |
|----------|-------|
| **Requisito Funcional** | RF-XXX |
| **Historia de Usuario** | MLS-XX |
| **Commits** | MLS-XX: ... (N commits) |
| **Tests** | test_xxx.py (N tests, X% cobertura) |
| **Aprobador** | @Dev1 |
| **Fecha Entrega Estimada** | YYYY-MM-DD |

### Criterios de Aceptación Cumplidos
- [x] Criterio 1
- [x] Criterio 2
- [x] Criterio 3

### Auditoría de Seguridad
- [x] No hay credenciales expuestas
- [x] Permisos validados en backend
- [x] Inputs validados
- [x] requirements.txt actualizado
```

---

## Relación Entre Elementos

```
RF-003 (Requisito)
├── MLS-3 (Historia de Usuario en Jira)
│   ├── Commit 1: MLS-3: Crear modelo
│   ├── Commit 2: MLS-3: Crear API
│   ├── Commit 3: MLS-3: Agregar tests
│   └── Commit 4: MLS-3: Agregar auditoría
│
├── Tests Automatizados
│   ├── test_empleados.py::TestEmpleadoUnitarios (5 tests)
│   ├── test_empleados.py::TestEmpleadoIntegracion (8 tests)
│   └── test_empleados.py::TestEmpleadoAceptacion (3 tests)
│
├── Pull Request #7
│   ├── Título: MLS-3: Implementar CRUD Empleados
│   ├── Reviewer: Dev 1
│   └── Status: Approved 
│
└── Merge a Main
    ├── Merged by: Dev 1
    ├── Timestamp: 2024-01-17 14:30
    └── Jira Status: Done 
```

---

## Checklist de Trazabilidad Completo

Antes de hacer merge a `main`:

- [ ] **RF identificado:** ¿Cuál requisito implementa este código?
- [ ] **HU/Ticket Jira:** ¿Qué ticket de Jira vincula esto?
- [ ] **Commits con ID:** Todos los commits incluyen ID-TICKET
- [ ] **Tests creados:** ¿Hay tests unitarios, integración y aceptación?
- [ ] **Cobertura:** ¿Mínimo 70% en funciones críticas?
- [ ] **PR con trazabilidad:** ¿PR incluye matriz de trazabilidad?
- [ ] **Criterios cumplidos:** ¿Se validan todos los AC de la HU?
- [ ] **Seguridad auditada:** ¿Se verificó gestión de secretos, validación, permisos?
- [ ] **Documentación:** ¿Está documentado en matriz y Jira?
- [ ] **Aprobación:** ¿PR aprobado por revisor?

---

## Ejemplo para Presentación Académica

Si necesitas presentar esto en defensa de tesis o proyecto:

```
3. IMPLEMENTACIÓN Y TRAZABILIDAD

3.1 Matriz de Trazabilidad

Se implementaron 5 requisitos funcionales en el Sprint 1:

| RF | Descripción | Status | Cobertura |
|----|----|--------|----------|
| RF-001 | Autenticación |  | 80% |
| RF-002 | Gestión de Roles |  | 75% |
| RF-003 | CRUD Empleados |  | 85% |
| RF-004 | Registro Actividades |  | 78% |
| RF-005 | Reportes |  | N/A |

3.2 Ejemplo: Requisito RF-003 (CRUD Empleados)

El requisito RF-003 se implementó a través de la historia MLS-3,
con 4 commits, 16 tests automatizados (85% cobertura),
y fue aprobado en el PR #7 por el revisor técnico.

Ver matriz completa: [link a matriz]
```

---

## Automatizar la Matriz

Si quieres generar la matriz automáticamente desde Git + Jira:

```python
# generate_traceability.py
import subprocess
import json

def get_commits_by_ticket(ticket):
    """Obtener commits de un ticket desde Git"""
    cmd = f"git log --grep={ticket} --oneline"
    resultado = subprocess.run(cmd, shell=True, capture_output=True)
    return resultado.stdout.decode().split('\n')

def get_tests_for_module(module):
    """Obtener tests asociados"""
    # Lógica para encontrar test_module.py
    pass

def generate_matrix():
    """Generar matriz completa"""
    tickets = ['MLS-1', 'MLS-2', 'MLS-3', 'MLS-4', 'MLS-5']
    
    for ticket in tickets:
        commits = get_commits_by_ticket(ticket)
        tests = get_tests_for_module(ticket.lower())
        # Generar fila
        print(f"| RF-00X | {ticket} | ... |")

if __name__ == '__main__':
    generate_matrix()
```

---

## Preguntas Frecuentes

**P: ¿Qué pasa si olvido incluir el ID de Jira en un commit?**
R: La trazabilidad se pierde. Usa `git commit --amend` si aún no has hecho push, o crea un nuevo commit aclarando.

**P: ¿Necesito matriz para cada pequeño commit?**
R: No, solo para features/PRs completas. Los commits pequeños se agrupan en el PR.

**P: ¿Quién es responsable de actualizar la matriz?**
R: El desarrollador cuando abre el PR, y el revisor verifica que esté correcta.

**P: ¿Cómo audito que se cumplieron todos los requisitos?**
R: Verifica que cada RF tenga al menos 1 test automatizado.

---

**¡La trazabilidad es la diferencia entre código caótico y profesional!** 
