#  Ejemplo Completo del Flujo de Trabajo Diario

## Escenario
**Matías** está tomando el ticket **MLS-3: CRUD de Empleados** para implementar la gestión de empleados en la plataforma de la Municipalidad de La Serena.

---

## Fase 1: Registrar la tarea en Jira

### 1. Matías abre el tablero Jira
- Accede a su tablero Jira
- Encuentra el ticket **MLS-3: CRUD de Empleados** en la columna **"To Do"** o **"Backlog"**

### 2. Marcar como "En Progreso"
- Arrastra la tarjeta desde **"To Do"** a **"In Progress"**
- El resto del equipo ahora ve que Matías está trabajando en esto

**Resultado:** El ticket está bloqueado para otros desarrolladores y nadie más lo tocará.

---

## Fase 2: Preparar el Entorno Local

Matías abre **PowerShell** en VS Code (Terminal > New Terminal) o en su línea de comandos.

### 1. Asegurar que está en la rama main
```powershell
git checkout main
```

**Salida esperada:**
```
Switched to branch 'main'
Your branch is up to date with 'origin/main'.
```

### 2. Obtener la última versión del código
```powershell
git pull origin main
```

**Salida esperada:**
```
Already up to date.
```

O si hay cambios:
```
From github.com:daninson7985/La_serena
 * branch            main       -> FETCH_HEAD
Updating abc1234..def5678
Fast-forward
 README.md | 5 ++++
 1 file changed, 5 insertions(+)
```

### 3. Crear una rama de trabajo
```powershell
git checkout -b feature/MLS-3-gestion-empleados
```

**Salida esperada:**
```
Switched to a new branch 'feature/MLS-3-gestion-empleados'
```

**Verificación:** En VS Code, esquina inferior izquierda debe mostrar `feature/MLS-3-gestion-empleados`

---

## ️ Fase 3: Desarrollar la Funcionalidad

Matías ahora está en su rama personal. Todo lo que haga aquí NO afecta a otros hasta que lo suba.

### Paso 1: Crear el modelo de Empleados

Matías abre `config/models.py` (o crea `apps/empleados/models.py`) y escribe:

```python
from django.db import models
from django.core.validators import RegexValidator

class Empleado(models.Model):
    ROLES = [
        ('admin', 'Administrador'),
        ('empleado', 'Empleado'),
        ('viewer', 'Solo Lectura'),
    ]
    
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    rut = models.CharField(
        max_length=12,
        unique=True,
        validators=[RegexValidator(r'^\d{1,2}\.\d{3}\.\d{3}-[0-9K]$')]
    )
    email = models.EmailField(unique=True)
    rol = models.CharField(max_length=20, choices=ROLES)
    fecha_contratacion = models.DateField()
    activo = models.BooleanField(default=True)
    
    def __str__(self):
        return f"{self.nombre} {self.apellido} ({self.rut})"
    
    class Meta:
        ordering = ['apellido', 'nombre']
```

**VS Code muestra automáticamente:** `feature/MLS-3-gestion-empleados` en la rama

### Paso 2: Crear migraciones

```powershell
python manage.py makemigrations
```

**Salida esperada:**
```
Migrations for 'empleados':
  empleados/migrations/0001_initial.py
    - Create model Empleado
```

### Paso 3: Aplicar migraciones a la BD local

```powershell
python manage.py migrate
```

**Salida esperada:**
```
Operations to perform:
  Apply all migrations: admin, auth, contenttypes, empleados, sessions
Running migrations:
  Applying empleados.0001_initial... OK
```

### Paso 4: Crear serializador

Matías crea `apps/empleados/serializers.py`:

```python
from rest_framework import serializers
from .models import Empleado

class EmpleadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empleado
        fields = ['id', 'nombre', 'apellido', 'rut', 'email', 'rol', 'fecha_contratacion', 'activo']
        read_only_fields = ['id']
```

### Paso 5: Crear vistas

Matías crea `apps/empleados/views.py`:

```python
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Empleado
from .serializers import EmpleadoSerializer

class EmpleadoViewSet(viewsets.ModelViewSet):
    queryset = Empleado.objects.all()
    serializer_class = EmpleadoSerializer
    
    @action(detail=False, methods=['get'])
    def activos(self, request):
        empleados = Empleado.objects.filter(activo=True)
        serializer = self.get_serializer(empleados, many=True)
        return Response(serializer.data)
```

### Paso 6: Instalar dependencias si es necesario

Si Matías necesita instalar una librería para el manejo de imágenes de perfil:

```powershell
pip install pillow
```

**Actualizar requirements.txt:**

```powershell
pip freeze > requirements.txt
```

Esto asegura que cuando Dev 1 o Dev 2 hagan `git pull`, también instalen `pillow`.

---

## Fase 4: Hacer Commits Enlazados

Matías ha completado la funcionalidad. Ahora prepara los cambios para subirlos.

### Primer commit: Modelo y migraciones

```powershell
git add .
git commit -m "MLS-3: Agrega modelo de empleados y validación de RUT único"
```

**Salida esperada:**
```
[feature/MLS-3-gestion-empleados abc1234] MLS-3: Agrega modelo de empleados y validación de RUT único
 2 files changed, 45 insertions(+)
 create mode 100644 empleados/models.py
 create mode 100644 empleados/migrations/0001_initial.py
```

**Nota:**  El mensaje comienza con `MLS-3:` - esto es OBLIGATORIO

### Segundo commit: Serializer y vistas

```powershell
git add .
git commit -m "MLS-3: Implementa serializer y viewset para CRUD de empleados"
```

**Salida esperada:**
```
[feature/MLS-3-gestion-empleados def5678] MLS-3: Implementa serializer y viewset para CRUD de empleados
 2 files changed, 38 insertions(+)
 create mode 100644 empleados/serializers.py
 create mode 100644 empleados/views.py
```

### Tercer commit: Actualizar requerimientos

Si instaló pillow:

```powershell
git add requirements.txt
git commit -m "MLS-3: Agregar pillow a dependencias para manejo de imágenes de perfil"
```

---

## Fase 5: Subir a GitHub

Matías sube su rama al repositorio remoto:

```powershell
git push origin feature/MLS-3-gestion-empleados
```

**Salida esperada:**
```
Enumerating objects: 12, done.
Counting objects: 100% (12/12), done.
Delta compression using up to 8 threads
Compressing objects: 100% (8/8), done.
Writing objects: 100% (8/8), 2.34 KiB | 2.34 MiB/s, done.
Total 12 (delta 5), reused 0 (delta 0), pack-reused 0
remote: Resolving deltas: 100% (5/5), done.
remote: 
remote: Create a pull request for 'feature/MLS-3-gestion-empleados' on GitHub.
remote:   https://github.com/daninson7985/La_serena/pull/new/feature/MLS-3-gestion-empleados
remote:
To github.com:daninson7985/La_serena.git
 * [new branch]      feature/MLS-3-gestion-empleados -> feature/MLS-3-gestion-empleados
```

 **Nota:** Si es la primera vez desde su equipo, GitHub abrirá una ventana en el navegador pidiendo autorización. Solo acepta y vuelve a intentar el `git push`.

---

## Fase 6: Crear un Pull Request (PR) en GitHub

### 1. Matías va a GitHub
- URL: https://github.com/daninson7985/La_serena

### 2. GitHub detecta automáticamente el push
- Aparece un botón dorado: **"Compare & pull request"**
- O va a la sección **"Pull requests"** y hace clic en **"New pull request"**

### 3. Configurar el PR
- **Base:** `main` (rama destino)
- **Compare:** `feature/MLS-3-gestion-empleados` (tu rama)

### 4. Llenar la descripción
```
## Descripción
Implementa el CRUD completo de empleados para la plataforma de la Municipalidad.

## Cambios realizados
-  Modelo de Empleados con validación de RUT
-  Serializador para API REST
-  ViewSet con endpoints de CRUD
-  Acción personalizada para filtrar empleados activos

## Archivos modificados
- empleados/models.py (nuevo)
- empleados/serializers.py (nuevo)
- empleados/views.py (nuevo)
- requirements.txt (actualizado)

## Cómo probar
1. Hacer migraciones: `python manage.py migrate`
2. Probar endpoints con Postman o curl:
   - GET /api/empleados/ (listar todos)
   - GET /api/empleados/activos/ (listar solo activos)
   - POST /api/empleados/ (crear nuevo)
   - PUT /api/empleados/{id}/ (actualizar)
   - DELETE /api/empleados/{id}/ (eliminar)

## Vinculado a
MLS-3
```

### 5. Asignar revisores
- Haz clic en **"Reviewers"**
- Selecciona a Dev 1 y Dev 2 para que revisen

### 6. Crear el PR
- Haz clic en **"Create Pull Request"**

**Resultado automático en Jira:**
- El ticket **MLS-3** se vincula automáticamente al PR
- Aparece un comentario: "PR abierto: feature/MLS-3-gestion-empleados"

---

## Fase 7: Revisión y Merge

### Escenario A: Aprobación sin cambios
1. Dev 1 revisa el código en GitHub
2. Comenta: "Looks good! "
3. Approeba el PR
4. Haz clic en **"Merge pull request"**

**Resultado:** Tu código está ahora en `main` y disponible para que lo descarguen Dev 2 y Dev 3.

### Escenario B: Se piden cambios
1. Dev 1 comenta: "¿Validaste el formato del RUT con X y Z?"
2. Vuelves a VS Code
3. Haces los cambios en tu rama `feature/MLS-3-gestion-empleados`
4. Commit y push: 
   ```powershell
   git add .
   git commit -m "MLS-3: Mejora validación de RUT según feedback"
   git push origin feature/MLS-3-gestion-empleados
   ```
5. El PR se actualiza automáticamente
6. Comenta: "Cambios realizados "

---

## Resumiendo el flujo completo

| Fase | Acción | Comando |
|------|--------|---------|
| 1 | Tomar ticket en Jira | Arrastra a "In Progress" |
| 2a | Ir a rama main | `git checkout main` |
| 2b | Actualizar código | `git pull origin main` |
| 2c | Crear rama personal | `git checkout -b feature/MLS-3-...` |
| 3 | Programar funcionalidad | Editar archivos, migrations, etc. |
| 4a | Primer commit | `git commit -m "MLS-3: ..."` |
| 4b | Segundo commit | `git commit -m "MLS-3: ..."` |
| 5 | Subir a GitHub | `git push origin feature/MLS-3-...` |
| 6 | Crear PR | GitHub → "Create Pull Request" |
| 7 | Esperar revisión | Equipo revisa en GitHub |
| 8 | Merge | "Merge pull request" (si aprobó) |
| 9 | En Jira | Mover ticket a "Done" |

---

## Puntos Clave a Recordar

 **SIEMPRE:**
- Comienza cada tarea con `git checkout main` + `git pull origin main`
- Crea una rama nueva con el ID del ticket: `feature/MLS-XX-descripcion`
- Incluye el ID del ticket en CADA commit: `MLS-3: ...`
- Haz commits pequeños y frecuentes
- Sube a GitHub regularmente con `git push origin`

 **NUNCA:**
- Programes directamente en `main`
- Hagas commits sin el ID del ticket
- Olvides hacer `git pull` antes de empezar
- Instales librerías sin actualizar `requirements.txt`
- Dejes cambios sin guardar (stage + commit)

---

## 🆘 Preguntas Frecuentes

**P: ¿Qué pasa si me equivoco en el nombre de la rama?**
R: No importa, creas una rama nueva:
```powershell
git checkout -b feature/MLS-3-gestion-empleados-correcto
git push origin feature/MLS-3-gestion-empleados-correcto
```

**P: ¿Puedo hacer commits sin el ID del ticket?**
R: Técnicamente sí, pero Jira no lo vinculará. Siempre incluye el ID.

**P: ¿Qué si alguien más modifica `main` mientras estoy trabajando?**
R: No te afecta porque estás en tu rama. Cuando hagas merge, Git combinará automáticamente.

**P: ¿Cómo actualizo mi rama si `main` cambió?**
R: Si necesitas los cambios de otros:
```powershell
git fetch origin
git rebase origin/main
```
O más simple:
```powershell
git pull origin main
```

---

**¡Felicidades! Ya entiendes el flujo completo del equipo.** 
