#  Guía: Pull Requests y Revisión de Código

Este documento detalla el proceso completo de crear un Pull Request (PR), revisarlo como administrador del proyecto, hacer merge y limpiar el repositorio.

---

## Escenario

**Matías** ha terminado de desarrollar el ticket **MLS-3: CRUD de Empleados**, ha hecho commits con el ID de Jira, y ha ejecutado `git push origin feature/MLS-3-gestion-empleados`.

Ahora debe crear un PR para que tú (como administrador) revises y apruebes el código.

---

## Paso 1: Crear el Pull Request en GitHub

### 1.1 Ir a GitHub
Matías abre su navegador y va a:
```
https://github.com/daninson7985/La_serena
```

### 1.2 GitHub detecta el push reciente
Verá un **banner verde** con el botón **"Compare & pull request"**:

```
feature/MLS-3-gestion-empleados (hace 2 minutos)
[Compare & pull request]  [Dismiss]
```

Matías hace clic en **"Compare & pull request"**

### 1.3 Llenar los detalles del PR

La página muestra:
- **Base:** `main` (rama destino)
- **Compare:** `feature/MLS-3-gestion-empleados` (rama del desarrollador)

#### Título del PR
Matías pone un título claro **comenzando con el ID de Jira**:

```
MLS-3: Agrega modelo de empleados y validación de RUT único
```

#### Descripción (Opcional pero recomendado)
```markdown
## Descripción
Implementa la funcionalidad completa del CRUD de empleados para la plataforma SGR.

## Cambios realizados
-  Modelo de Empleados con validación de RUT (formato XX.XXX.XXX-X)
-  Serializador REST para API
-  ViewSet con endpoints CRUD completos
-  Acción personalizada para filtrar empleados activos
-  Tests unitarios para validaciones

## Archivos modificados
- `empleados/models.py` (nuevo)
- `empleados/serializers.py` (nuevo)
- `empleados/views.py` (nuevo)
- `empleados/tests.py` (nuevo)
- `requirements.txt` (actualizado con pillow)

## Cómo probar
1. Realizar migraciones: `python manage.py migrate`
2. Crear empleado de prueba desde admin o API
3. Probar endpoints:
   - GET /api/empleados/ (listar todos)
   - GET /api/empleados/activos/ (listar activos)
   - POST /api/empleados/ (crear nuevo)
   - PUT /api/empleados/{id}/ (actualizar)
   - DELETE /api/empleados/{id}/ (eliminar)

## Vinculado a
Jira: MLS-3
```

### 1.4 Asignar Revisor

En la barra lateral derecha, Matías busca la sección **"Reviewers"** (o "Assignees" según la versión):

1. Hace clic en **"Add reviewers"** o el icono de persona
2. Busca tu nombre (por ejemplo: "Dev 1" o tu usuario de GitHub)
3. Te selecciona como revisor

**Resultado:** Recibirás una notificación de GitHub: "daninson7985 requested your review on MLS-3: ..."

### 1.5 Crear el PR
Matías hace clic en el botón verde **"Create pull request"**

**Confirmación:**
```
 Pull request #5 created successfully
Feature branch 'feature/MLS-3-gestion-empleados' into main
```

---

## Paso 2: Tu Rol - Revisión de Código

Recibes la notificación de que Matías solicitó tu revisión. Abres GitHub.

### 2.1 Entrar al Pull Request

1. Vas a tu repositorio: https://github.com/daninson7985/La_serena
2. Haces clic en la pestaña **"Pull requests"**
3. Abres el PR **MLS-3: Agrega modelo de empleados...**

### 2.2 Revisar los cambios

Haces clic en la pestaña **"Files changed"** para ver línea por línea qué cambió:

```
empleados/models.py
+++ b/empleados/models.py
@@ -0,0 +1,35 @@
+from django.db import models
+from django.core.validators import RegexValidator
+
+class Empleado(models.Model):
+    ROLES = [
+        ('admin', 'Administrador'),
+        ('empleado', 'Empleado'),
+        ('viewer', 'Solo Lectura'),
+    ]
+    
+    nombre = models.CharField(max_length=100)
+    apellido = models.CharField(max_length=100)
+    rut = models.CharField(
+        max_length=12,
+        unique=True,
+        validators=[RegexValidator(r'^\d{1,2}\.\d{3}\.\d{3}-[0-9K]$')]
+    )
    ...
```

### 2.3 Checklist de Revisión

Verifica estos puntos antes de aprobar:

#### Seguridad 
- [ ] No hay contraseñas, API keys, o tokens expuestos
- [ ] No hay datos sensibles en comentarios o logs
- [ ] Las validaciones de entrada son correctas (ej. RUT)
- [ ] Las consultas a BD no tienen inyección SQL

#### Calidad de Código 
- [ ] El código sigue la nomenclatura del proyecto
- [ ] Las funciones tienen nombres descriptivos
- [ ] No hay código duplicado
- [ ] Los modelos tienen `__str__()` y `class Meta`

#### Dependencias 
- [ ] Si instalaron librerías, `requirements.txt` está actualizado
- [ ] Las versiones son compatibles con el resto del proyecto
- [ ] No hay dependencias innecesarias

#### Estándares del Proyecto SGR ️
- [ ] Sigue las reglas de la Municipalidad de La Serena
- [ ] Las validaciones de RUT son correctas
- [ ] Los permisos/roles están implementados
- [ ] Los datos municipales están protegidos

#### Tests 
- [ ] Hay tests unitarios para funciones críticas
- [ ] Los tests comprueban casos límite (RUT inválido, etc.)
- [ ] No hay tests que fallen

### 2.4 Si Todo Está Bien 

Haces clic en el botón **"Review changes"** (parte superior derecha):

```
[Review changes ▼]
```

Seleccionas:
- **Opción:** "Approve" (Aprobar)
- **Comentario (opcional):** 
  ```
  Great work! Todo se ve correcto. 
   Seguridad verificada
   requirements.txt actualizado
   Validaciones de RUT implementadas correctamente
   El equipo puede integrar
  ```

Haces clic en **"Submit review"**

**Resultado:**
```
 Approved by daninson7985
```

### 2.5 Si Hay Errores o Cambios Necesarios 

Mientras revisas, si ves algo que mejorar, haces clic en la línea para dejar un comentario:

**Ejemplo de comentario:**
```
Línea 15 (en models.py):
rut = models.CharField(max_length=12, unique=True, ...)

 Comentario: 
¿Validaste que este regex funciona con RUTs que tienen puntos y guión? 
Por ejemplo: 12.345.678-9

Sugerencia: Agregar test unitario para casos límite.
```

Luego haces clic en **"Review changes"** y seleccionas:
- **Opción:** "Request changes" (Solicitar cambios)
- **Comentario:** "Por favor, agrega validación adicional para RUTs"

**Resultado:**
```
 Changes requested by daninson7985
```

Matías verá tu comentario, hará los cambios en su rama local, y ejecutará:

```powershell
git add .
git commit -m "MLS-3: Mejora validación de RUT con tests adicionales"
git push origin feature/MLS-3-gestion-empleados
```

El PR se actualiza automáticamente. Revisas de nuevo y si todo está bien, apruebas.

---

## Paso 3: Hacer el Merge hacia `main`

Una vez que el PR está **Approved** :

### 3.1 Hacer merge

En la página del PR, desplázate hacia abajo y verás el botón **"Merge pull request"**:

```
[Merge pull request ▼]  [Squash and merge]  [Rebase and merge]
```

Haces clic en **"Merge pull request"** (la opción por defecto está bien)

### 3.2 Confirmar el merge

GitHub te pide confirmación:

```
Merge pull request #5 into main?

[Confirm merge]  [Cancel]
```

Haces clic en **"Confirm merge"**

### 3.3 Resultado

GitHub muestra:

```
 Pull request successfully merged and closed
You can now safely delete the 'feature/MLS-3-gestion-empleados' branch.
[Delete branch]
```

### 3.4 Integración con Jira 

**Magia automática:**

Gracias a que tu commits incluyen `MLS-3:` en el mensaje, GitHub y Jira están conectados. 

En tu tablero Jira, el ticket **MLS-3: CRUD de Empleados** cambia automáticamente:

```
 Columna anterior: In Progress
 Columna nueva: Done 

El ticket tiene un comentario automático:
"Commit(s) incluido en main:
- MLS-3: Agrega modelo de empleados y validación de RUT único
- MLS-3: Implementa serializer y viewset para CRUD
- MLS-3: Agrega pillow a dependencias"
```

El equipo en Jira ve que tu trabajo está completado y listo en producción.

---

## Paso 4: Sincronizar y Limpiar (Todo el Equipo)

Después de que el merge está hecho, **todos** en el equipo (incluyendo a Matías) deben sincronizar sus repositorios locales y eliminar ramas viejas.

### 4.1 Actualizar la rama main local

```powershell
git checkout main
```

**Salida esperada:**
```
Switched to branch 'main'
Your branch is behind 'origin/main' by 1 commit.
  (use "git pull" to update the branch)
```

```powershell
git pull origin main
```

**Salida esperada:**
```
From github.com:daninson7985/La_serena
   4238c16..72b59d8  main       -> origin/main
Updating 4238c16..72b59d8
Fast-forward
 empleados/models.py      | 35 +++++++++++++++++++++++++++++++++++
 empleados/serializers.py | 12 ++++++++++++
 empleados/views.py       | 25 +++++++++++++++++++++++++
 requirements.txt         |  1 +
 4 files changed, 73 insertions(+)
 create mode 100644 empleados/models.py
 create mode 100644 empleados/serializers.py
 create mode 100644 empleados/views.py
```

### 4.2 Eliminar la rama de trabajo

La rama `feature/MLS-3-gestion-empleados` ya no se necesita. Es seguro eliminarla localmente:

```powershell
git branch -d feature/MLS-3-gestion-empleados
```

**Salida esperada:**
```
Deleted branch feature/MLS-3-gestion-empleados (was 4abc123).
```

### 4.3 Verificar ramas activas

Para ver qué ramas tienes activas:

```powershell
git branch -a
```

**Salida esperada:**
```
* main
  remotes/origin/main
```

Solo `main` debe estar visible. Las ramas de trabajo ya fueron eliminadas. 

---

## Flujo Completo Resumido

| Persona | Acción | Comando/Donde |
|---------|--------|---------------|
| **Matías (Desarrollador)** | Termina desarrollo en rama | `git push origin feature/MLS-3-...` |
| **Matías** | Crea PR en GitHub | Click "Compare & pull request" |
| **Matías** | Asigna revisor | GitHub → Reviewers → Selecciona Dev 1 |
| **Dev 1 (Tú)** | Recibe notificación | Email/GitHub notifications |
| **Dev 1** | Revisa código | GitHub → PR → Files changed |
| **Dev 1** | Aprueba o solicita cambios | "Approve" o "Request changes" |
| **Dev 1** | Hace merge | Click "Merge pull request" |
| **Jira** | Ticket actualiza automáticamente | Estado cambia a "Done" |
| **Matías** | Sincroniza su main | `git checkout main; git pull` |
| **Matías** | Limpia rama antigua | `git branch -d feature/MLS-3-...` |
| **Equipo** | Todos actualizan main | `git pull origin main` |

---

## Errores Comunes

### Error: "This branch has conflicts that must be resolved"

**Causa:** Tu rama tiene cambios que entran en conflicto con main

**Solución (Matías hace esto):**
```powershell
# En su máquina
git fetch origin
git rebase origin/main
# Resolver conflictos manualmente
git add .
git rebase --continue
git push origin feature/MLS-3-gestion-empleados --force
```

### Error: "Cannot delete branch while on it"

**Causa:** Intentas borrar la rama que estás usando

**Solución:**
```powershell
git checkout main
git branch -d feature/MLS-3-gestion-empleados
```

### Error: "Branch not found"

**Causa:** Intentas eliminar una rama que no existe

**Verificar qué ramas tienes:**
```powershell
git branch -a
```

---

## Checklist Final

Después de completar todo el ciclo:

- [ ] PR creado con título claro (ID-TICKET: Descripción)
- [ ] Revisor asignado
- [ ] Cambios revisados en "Files changed"
- [ ] Checklist de revisión completado
- [ ] PR aprobado (Approve)
- [ ] Merge ejecutado en GitHub
- [ ] Ticket en Jira cambió a "Done"
- [ ] Todos en el equipo ejecutaron `git pull origin main`
- [ ] Rama local eliminada con `git branch -d feature/MLS-X-...`
- [ ] Código listo para la siguiente tarea

---

## Pro Tips

 **Siempre:**
- Incluye el ID de Jira en el título del PR
- Solicita revisión a alguien del equipo
- Actualiza requirements.txt si instales librerías
- Limpia ramas después de merge

 **Nunca:**
- Hagas merge a ti mismo sin que alguien revise
- Olvides eliminar ramas antiguas
- Dejes ramas sin mergear por mucho tiempo
- Cambies requirements.txt sin documentar por qué

---

**¡Felicidades! Completaste el ciclo completo de desarrollo.** 
