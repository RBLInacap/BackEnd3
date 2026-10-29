#  CHEATSHEET - Comandos y Buenas Prácticas Rápidas

**Referencia rápida para el equipo de desarrollo.** Copiar y pegar cuando sea necesario.

---

## Comandos Esenciales de Git

### Primeras Veces (Setup Inicial)

```powershell
# Clonar repositorio (Dev 2 y Dev 3)
git clone https://github.com/daninson7985/La_serena.git
cd La_serena

# Crear entorno virtual
python -m venv venv
.\venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Migraciones iniciales
python manage.py migrate

# Verificar que funciona
python manage.py runserver
```

### Antes de Cada Tarea

```powershell
# 1. Ir a rama main
git checkout main

# 2. Actualizar código
git pull origin main

# 3. Crear rama de trabajo (reemplaza MLS-3 y descripcion)
git checkout -b feature/MLS-3-gestion-empleados
```

### Desarrollar y Guardar

```powershell
# Ver cambios
git status

# Agregar todos los cambios
git add .

# Commit con referencia a Jira (OBLIGATORIO incluir ID)
git commit -m "MLS-3: Agrega modelo de Empleados"

# Hacer múltiples commits
git commit -m "MLS-3: Implementar serializer"
git commit -m "MLS-3: Crear endpoints REST"

# Ver commits
git log --oneline
```

### Subir a GitHub

```powershell
# Primera vez (crear rama remota)
git push -u origin feature/MLS-3-gestion-empleados

# Siguientes veces (actualizar rama remota)
git push origin feature/MLS-3-gestion-empleados

# Forzar push (solo si sabes qué haces)
git push origin feature/MLS-3-gestion-empleados --force
```

### Después del Merge

```powershell
# Volver a main
git checkout main

# Actualizar con cambios mergeados
git pull origin main

# Eliminar rama local (después del merge en GitHub)
git branch -d feature/MLS-3-gestion-empleados

# Eliminar rama remota (si GitHub no lo hace automáticamente)
git push origin --delete feature/MLS-3-gestion-empleados

# Ver todas las ramas
git branch -a
```

### Solucionar Problemas Comunes

```powershell
# Si hiciste cambios en main por error, crea rama nueva
git checkout -b feature/MLS-4-descripcion
# Ahora tu rama tiene esos cambios

# Descartar cambios locales
git restore nombre-archivo.py

# Ver qué va a hacer git
git diff  # Cambios sin staged
git diff --staged  # Cambios ya staged (git add)

# Deshacer último commit (mantiene cambios)
git reset HEAD~1

# Ver quién cambió cada línea
git blame archivo.py
```

---

## Comandos Django Esenciales

### Setup Inicial

```powershell
# Crear proyecto
django-admin startproject config .

# Crear app
python manage.py startapp nombre_app

# Activar app en settings.py
# Agregar 'nombre_app' a INSTALLED_APPS
```

### Migraciones

```powershell
# Ver cambios pendientes en modelos
python manage.py makemigrations

# Aplicar cambios a la BD
python manage.py migrate

# Ver historial de migraciones
python manage.py showmigrations

# Deshacer última migración
python manage.py migrate nombre_app 0002  # Volver a versión anterior
```

### Servidor y Pruebas

```powershell
# Iniciar servidor de desarrollo
python manage.py runserver

# Iniciar servidor en otro puerto
python manage.py runserver 8001

# Ejecutar todos los tests
python manage.py test

# Ejecutar tests de una app específica
python manage.py test nombre_app

# Ejecutar test específico
python manage.py test nombre_app.tests.TestClass.test_metodo

# Tests con cobertura
coverage run --source='.' manage.py test
coverage report
coverage html

# Ver reporte en navegador
start htmlcov/index.html
```

### Admin y Usuarios

```powershell
# Crear super usuario
python manage.py createsuperuser

# Cambiar contraseña
python manage.py changepassword nombre_usuario

# Shell interactivo de Django
python manage.py shell
```

---

## Pip: Gestión de Dependencias

```powershell
# Instalar librería
pip install nombre-libreria

# Instalar versión específica
pip install nombre-libreria==1.2.3

# Listar instaladas
pip list

# Generar requirements.txt SIEMPRE después de instalar
pip freeze > requirements.txt

# Instalar desde requirements
pip install -r requirements.txt

# Actualizar librería
pip install --upgrade nombre-libreria

# Buscar librería
pip search nombre-libreria
```

---

## Testing: Quick Reference

### Estructura Básica

```python
from django.test import TestCase

class TestMiModelo(TestCase):
    def setUp(self):
        """Datos iniciales antes de cada test"""
        pass
    
    def test_algo(self):
        """Test debe empezar con 'test_'"""
        self.assertEqual(1, 1)
    
    def test_otro(self):
        self.assertTrue(True)
```

### Ejecutar Tests

```powershell
# Todos
python manage.py test

# Una app
python manage.py test nombre_app

# Una clase
python manage.py test nombre_app.tests.TestClass

# Un método
python manage.py test nombre_app.tests.TestClass.test_metodo

# Verbose (muestra más detalles)
python manage.py test -v 2

# Solo fallos
python manage.py test --keepdb
```

### Assertions Comunes

```python
# Igualdad
self.assertEqual(a, b)
self.assertNotEqual(a, b)

# Booleanos
self.assertTrue(x)
self.assertFalse(x)

# Excepciones
with self.assertRaises(Exception):
    funcion_que_falla()

# Contención
self.assertIn(a, b)
self.assertNotIn(a, b)

# None
self.assertIsNone(x)
self.assertIsNotNone(x)
```

---

## Variables de Entorno (.env)

### Crear .env

```
DJANGO_SECRET_KEY=tu-clave-secreta-abc123xyz
DEBUG=True
DATABASE_NAME=db.sqlite3
DATABASE_USER=usuario_bd
DATABASE_PASSWORD=password_bd
ALLOWED_HOSTS=localhost,127.0.0.1
JIRA_API_KEY=tu-api-key
GITHUB_TOKEN=tu-token
```

### Usar en Django

```python
# settings.py
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost').split(',')
```

### Instalar python-dotenv

```powershell
pip install python-dotenv
pip freeze > requirements.txt
```

---

## Mensajes de Commit: Formato Obligatorio

### CORRECTO

```
MLS-3: Agrega modelo de Empleados
MLS-3: Implementar serializer REST
MLS-5: Crear validaciones de RUT
MLS-7: Agregar tests unitarios para CRUD
```

### INCORRECTO

```
cambios
fix bug
actualización
MLS3 Agrega modelo  (sin guión)
Agrega modelo (sin ID de Jira)
```

### Regla de Oro

```
TICKET-ID: Descripción clara en presente

Ejemplo: MLS-3: Agrega modelo de Empleados
         ↑     ↑ Espacio
         |     └─ Verbo en presente
         └─ ID de Jira (OBLIGATORIO)
```

---

## Estructura de Carpetas Recomendada

```
La_serena/
├── config/                 # Configuración del proyecto
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── apps/                   # Aplicaciones de Django
│   ├── empleados/
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── serializers.py
│   │   ├── tests.py
│   │   └── migrations/
│   │
│   ├── actividades/
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── serializers.py
│   │   ├── tests.py
│   │   └── migrations/
│   │
│   └── reportes/
│       ├── models.py
│       ├── views.py
│       └── tests.py
│
├── tests/                  # Tests globales
│   ├── test_unitarios.py
│   ├── test_integracion.py
│   └── test_aceptacion.py
│
├── static/                 # CSS, JS, imágenes
│   └── ...
│
├── media/                  # Archivos subidos
│   └── ...
│
├── templates/              # HTML
│   └── ...
│
├── .env                    # Variables de entorno (NO SUBIR A GIT)
├── .gitignore
├── manage.py
├── requirements.txt
├── README.md
├── CONTRIBUTING.md
├── WORKFLOW-EXAMPLE.md
├── PULL_REQUEST-REVIEW.md
├── TESTING-STRATEGY.md
├── TESTING-TEMPLATES.md
├── TRACEABILITY-MATRIX.md
├── SETUP-JIRA-GITHUB.md
└── CHEATSHEET.md
```

---

## Flujo Completo de Una Tarea (De Punta a Punta)

```powershell
# 1. JIRA: Mover ticket a "In Progress"
# (Click en Jira - MLS-3)

# 2. LOCAL: Preparar rama
git checkout main
git pull origin main
git checkout -b feature/MLS-3-gestion-empleados

# 3. LOCAL: Desarrollar
# (Editar archivos, crear modelos, etc.)

# 4. LOCAL: Crear migraciones
python manage.py makemigrations
python manage.py migrate

# 5. LOCAL: Escribir tests
python manage.py test
coverage run --source='.' manage.py test

# 6. LOCAL: Commits regulares
git add .
git commit -m "MLS-3: Agrega modelo de Empleados"
git commit -m "MLS-3: Implementa serializer y viewset"

# 7. GITHUB: Subir rama
git push -u origin feature/MLS-3-gestion-empleados

# 8. GITHUB: Crear Pull Request
# Click "Compare & pull request"
# Título: MLS-3: Agrega modelo de Empleados
# Descripción: Explicar cambios
# Asignar revisor

# 9. CODE REVIEW: Esperar aprobación

# 10. GITHUB: Merge Pull Request
# Click "Merge pull request"
# (Automáticamente en Jira: MLS-3 → Done)

# 11. LOCAL: Sincronizar y limpiar
git checkout main
git pull origin main
git branch -d feature/MLS-3-gestion-empleados
```

---

## Checklist Antes de Hacer Push

- [ ] Incluye ID de Jira en cada commit: `MLS-XX:`
- [ ] Tests ejecutados: `python manage.py test` (sin errores)
- [ ] Cobertura >= 70%: `coverage report`
- [ ] `requirements.txt` actualizado (si instalaste librerías)
- [ ] Migraciones generadas: `python manage.py makemigrations`
- [ ] Servidor funciona: `python manage.py runserver`
- [ ] No hay archivos `.env` en git
- [ ] Rama correcta: `git branch` (debe ser `feature/MLS-X-...`)
- [ ] Remote correcto: `git remote -v`

---

## 🆘 Casos de Emergencia

### Accidentalmente commiteé en main

```powershell
# 1. Crear rama nueva
git checkout -b feature/MLS-3-descripcion

# 2. main está limpio
git checkout main
git reset HEAD~1  # Deshacer último commit en main

# 3. Cambios están en feature/MLS-3-descripcion
# (Continúa el flujo normal)
```

### Necesito descartar cambios

```powershell
# Archivo específico
git restore archivo.py

# Todos los cambios
git restore .

# Changes ya staged
git restore --staged archivo.py
```

### Mi rama está atrasada de main

```powershell
# Traer cambios de main
git pull origin main

# Si hay conflictos, editarlos manualmente y luego:
git add .
git commit -m "MLS-3: Resolver conflictos con main"
git push origin feature/MLS-3-descripcion
```

### Necesito cambiar el mensaje del último commit

```powershell
# Antes de hacer push
git commit --amend -m "MLS-3: Nuevo mensaje"

# Después de hacer push (fuerza)
git commit --amend -m "MLS-3: Nuevo mensaje"
git push origin feature/MLS-3-descripcion --force
```

---

## Matriz de Decisión Rápida

| Pregunta | Respuesta | Comando |
|----------|-----------|---------|
| ¿Qué cambié? | Ver diferencias | `git diff` |
| ¿Tengo cambios sin guardar? | Ver estado | `git status` |
| ¿Necesito actualizar mi rama? | Traer cambios de main | `git pull origin main` |
| ¿Hice commit en rama equivocada? | Crear rama nueva | `git checkout -b feature/MLS-X-...` |
| ¿Quiero deshacer cambios? | Restaurar archivo | `git restore archivo.py` |
| ¿Tests no pasan? | Ejecutar con verbose | `python manage.py test -v 2` |
| ¿Necesito generar reporte HTML? | Coverage | `coverage html; start htmlcov/index.html` |
| ¿Olvidé .env en git? | Quitar del cache | `git rm --cached .env` |

---

## Documentación Completa

- **Setup inicial:** [SETUP-JIRA-GITHUB.md](SETUP-JIRA-GITHUB.md)
- **Onboarding:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Ejemplo paso a paso:** [WORKFLOW-EXAMPLE.md](WORKFLOW-EXAMPLE.md)
- **PRs y revisión:** [PULL_REQUEST-REVIEW.md](PULL_REQUEST-REVIEW.md)
- **Testing:** [TESTING-STRATEGY.md](TESTING-STRATEGY.md)
- **Templates de código:** [TESTING-TEMPLATES.md](TESTING-TEMPLATES.md)
- **Trazabilidad:** [TRACEABILITY-MATRIX.md](TRACEABILITY-MATRIX.md)

---

## Pro Tips

 **SIEMPRE:**
- Commit pequeños y frecuentes
- Include ID de Jira
- Tests antes de push
- Actualiza requirements.txt
- Revisa `git status` antes de commitar

 **NUNCA:**
- Commiteá en main directamente
- Subas .env a git
- Ignores tests que fallan
- Olvides hacer `git pull` antes de crear rama
- Hagas force push sin coordinar

---

**¡Referencia rápida actualizada!** 
