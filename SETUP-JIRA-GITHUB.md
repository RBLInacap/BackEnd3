# Fase 1: Setup de Jira y GitHub - Infraestructura del Proyecto

Esta guía cubre la configuración inicial de Jira y GitHub para que el equipo pueda comenzar a colaborar en el proyecto SGR.

---

## Resumen

Antes de escribir código, necesitas:
1. Crear un proyecto en Jira
2. Crear un repositorio en GitHub
3. Integrar Jira con GitHub
4. Agregar miembros del equipo
5. Configurar tableros y columnas

**Tiempo estimado:** 30 minutos

---

## Paso 1: Crear Proyecto en Jira

### 1.1 Acceder a Jira

1. Ve a [https://www.atlassian.com/software/jira](https://www.atlassian.com/software/jira)
2. Si tienes cuenta de Inacap, usa tus credenciales
3. Si no, crea una cuenta gratuita en [jira.atlassian.net](https://jira.atlassian.net)

### 1.2 Crear un Espacio de Trabajo

Una vez logueado:

1. Haz clic en **"Create"** o **"Create project"**
2. Selecciona el tipo de proyecto:
   - **Scrum** (si trabajas con sprints de 2 semanas)
   - **Kanban** (si prefieres flujo continuo)

**Recomendación para SGR:** Scrum con sprints de 2 semanas

### 1.3 Configurar Detalles del Proyecto

```
Nombre del Proyecto: Municipalidad de La Serena - SGR
Clave del Proyecto: MLS (se genera automáticamente)
Descripción: Sistema de Gestión de Recursos para la Municipalidad
Tipo: Scrum
```

**Resultado esperado:**
- Clave: **MLS**
- Los tickets se denominarán: MLS-1, MLS-2, MLS-3, etc.

### 1.4 Configurar el Tablero

Jira crea un tablero por defecto con columnas:
- **Backlog** (tareas no iniciadas)
- **To Do** (listas para hacer)
- **In Progress** (en desarrollo)
- **Done** (completadas)

 Esto está bien. Si quieres añadir más columnas:

1. Ve a **Board Settings** (engranaje en esquina superior derecha)
2. Click en **Columns**
3. Puedes agregar: "Ready for Review", "In Review", etc.

**Para SGR recomendamos:**
```
Backlog → To Do → In Progress → In Review → Done
```

---

## Paso 2: Agregar Miembros del Equipo en Jira

### 2.1 Invitar al Equipo

1. Ve a **Project Settings** → **People**
2. Haz clic en **Add people**
3. Ingresa los emails de tus compañeros:
   - dev2@inacap.cl (Dev 2 / Matías)
   - dev3@inacap.cl (Dev 3)

### 2.2 Asignar Roles

```
Dev 1 (Tú):        Project Admin / Scrum Master
Dev 2 (Matías):    Developer
Dev 3:             Developer
```

**Resultado:** Todos pueden crear tickets, comentar y ver el avance.

---

## Paso 3: Crear Repositorio en GitHub

### 3.1 Crear un Nuevo Repositorio

1. Ve a [github.com](https://github.com)
2. Haz clic en **"+"** (arriba a la derecha)
3. Selecciona **"New repository"**

### 3.2 Configurar Detalles

```
Repository name:  La_serena
Description:      Sistema de Gestión de Recursos - Municipalidad
Visibility:       Private (o Public si es académico)
Initialize:        NO inicialices (lo haremos nosotros)
```

**Haz clic en "Create repository"**

**Resultado esperado:**
```
https://github.com/daninson7985/La_serena
```

### 3.3 Agregar Colaboradores

1. Ve a **Settings** → **Collaborators**
2. Haz clic en **"Add people"**
3. Invita:
   - dev2_username
   - dev3_username

**Permisos:** Todos deben tener acceso de **Write** (para hacer push)

---

## Paso 4: Integrar Jira con GitHub

Esta es la **parte más importante**: permite que commits y PRs se vinculen automáticamente a tickets.

### 4.1 Instalar Integración en Jira

1. En Jira, ve a **Project Settings** → **Integrations** (o **External apps**)
2. Busca **"GitHub for Jira"** o **"Atlassian for GitHub"**
3. Haz clic en **"Install"** o **"Connect"**
4. Se abrirá GitHub pidiendo autorización
5. **Autoriza** el acceso

### 4.2 Vincular Repositorio

Una vez autorizado:

1. En Jira, ve a **Project Settings** → **GitHub Repositories**
2. Haz clic en **"Connect GitHub Repository"**
3. Selecciona tu repositorio: `daninson7985/La_serena`
4. Haz clic en **"Connect"**

**Verificación:**
- En tu repositorio de GitHub, ve a **Settings** → **Applications**
- Deberías ver "Jira Software for GitHub" como autorizado

### 4.3 Probar la Integración

Una vez conectado:

1. Crea un commit con formato: `MLS-1: mensaje`
2. En Jira, ve al ticket MLS-1
3. Deberías ver la actividad del commit en el ticket automáticamente

**Ejemplo:**
```powershell
git commit -m "MLS-1: Configuración inicial del proyecto"
git push origin main
```

En Jira → MLS-1, aparecerá:
```
 Commit: "MLS-1: Configuración inicial del proyecto"
```

---

## Paso 5: Crear el Backlog Inicial

### 5.1 Crear Épicas (Historias Grandes)

En Jira, crea épicas para agrupar tareas:

```
ÉPICA 1: Autenticación y Gestión de Usuarios
  ├── MLS-1: Configuración inicial del proyecto
  ├── MLS-2: Modelo de Usuario
  ├── MLS-3: CRUD de Empleados
  └── MLS-4: Sistema de Roles y Permisos

ÉPICA 2: Registro de Actividades
  ├── MLS-5: Modelo de Actividades
  ├── MLS-6: API de Actividades
  └── MLS-7: Validaciones y Auditoría

ÉPICA 3: Reportes y Dashboards
  ├── MLS-8: Modelo de Reportes
  ├── MLS-9: Vistas de Dashboard
  └── MLS-10: Exportar a PDF
```

### 5.2 Crear Tickets en el Backlog

Para cada ticket:

1. Haz clic en **"Create Issue"** (o "Create" en el tablero)
2. Rellena:
   - **Type:** Story (para funcionalidades)
   - **Summary:** Descripción clara
   - **Description:** Criterios de aceptación
   - **Assignee:** A quién le asignas
   - **Epic Link:** A qué épica pertenece
   - **Story Points:** Complejidad (3, 5, 8)

**Ejemplo de ticket:**
```
Type: Story
Title: MLS-3: CRUD de Empleados

Descripción:
Como administrador del sistema
Quiero poder gestionar empleados (crear, editar, eliminar)
Para que la base de datos esté siempre actualizada

Criterios de Aceptación:
- [ ] Crear empleado con validación de RUT
- [ ] Editar datos de empleado
- [ ] Eliminar empleado (solo admin)
- [ ] No permitir RUT duplicado
- [ ] Auditar cambios en la BD

Story Points: 8
Assignee: Matías
```

---

## Paso 6: Flujo de Trabajo Inicial

### 6.1 Primer Commit y Setup (Dev 1)

```powershell
# En tu máquina local
mkdir La_serena
cd La_serena
python -m venv venv
.\venv\Scripts\activate
pip install django
django-admin startproject config .
pip freeze > requirements.txt

# Inicializar Git
git init
git branch -M main
git add .
git commit -m "MLS-1: Configuración inicial del proyecto Django"

# Vincular con GitHub
git remote add origin https://github.com/daninson7985/La_serena.git
git push -u origin main
```

### 6.2 Verificar Integración

En Jira:
1. Ve al ticket **MLS-1**
2. Deberías ver:
   ```
    Commit: "MLS-1: Configuración inicial del proyecto Django"
   ```

Si lo ves, ¡la integración funciona! 

---

## Checklist de Configuración Completada

- [ ] Proyecto creado en Jira (clave: MLS)
- [ ] Repositorio creado en GitHub (La_serena)
- [ ] Dev 2 y Dev 3 agregados a Jira
- [ ] Dev 2 y Dev 3 agregados a GitHub (Collaborators)
- [ ] Integración Jira-GitHub conectada
- [ ] Tablero configurado con columnas (Backlog → To Do → In Progress → In Review → Done)
- [ ] Backlog inicial creado (al menos 5 tickets)
- [ ] Primer commit pusheado a GitHub
- [ ] Integración verificada (commit visible en Jira)
- [ ] README.md creado en repositorio

---

## Crear README.md en GitHub

Crea un archivo `README.md` en la raíz del proyecto:

```markdown
# Sistema de Gestión de Recursos (SGR) - Municipalidad de La Serena

Plataforma web para la gestión de recursos municipales, actividades y monitoreo de metas.

## Inicio Rápido

### Requisitos
- Python 3.8+
- Git
- VS Code

### Instalación

1. Clonar el repositorio:
   ```bash
   git clone https://github.com/daninson7985/La_serena.git
   cd La_serena
   ```

2. Crear entorno virtual:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate  # Windows
   source venv/bin/activate  # Linux/Mac
   ```

3. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

4. Ejecutar migraciones:
   ```bash
   python manage.py migrate
   ```

5. Iniciar servidor:
   ```bash
   python manage.py runserver
   ```

6. Abrir en navegador: http://127.0.0.1:8000/

## Documentación

- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Guía de configuración
- **[WORKFLOW-EXAMPLE.md](WORKFLOW-EXAMPLE.md)** - Ejemplo de flujo de trabajo
- **[PULL_REQUEST-REVIEW.md](PULL_REQUEST-REVIEW.md)** - Proceso de revisión
- **[TESTING-STRATEGY.md](TESTING-STRATEGY.md)** - Estrategia de pruebas
- **[TESTING-TEMPLATES.md](TESTING-TEMPLATES.md)** - Templates de tests
- **[TRACEABILITY-MATRIX.md](TRACEABILITY-MATRIX.md)** - Matriz de trazabilidad
- **[CHEATSHEET.md](CHEATSHEET.md)** - Comandos rápidos

## Jira y GitHub

- **Proyecto Jira:** [MLS en Jira](https://tu-jira-instance.atlassian.net)
- **Repositorio:** https://github.com/daninson7985/La_serena

## Equipo

- **Dev 1 (Líder):** Tu Nombre
- **Dev 2:** Matías
- **Dev 3:** Nombre

## Stack Tecnológico

- **Backend:** Django 4.x + Python 3.8+
- **Base de Datos:** SQLite (desarrollo) / PostgreSQL (producción)
- **API:** Django REST Framework
- **Testing:** Django Test Framework + Coverage
- **Control de Versiones:** Git + GitHub
- **Gestión:** Jira + GitHub

## Contacto

Para preguntas sobre el proyecto, contacta a Dev 1 o abre una issue en GitHub.

---

**Última actualización:** 2024-01-15
```

---

## Seguridad: .gitignore

Asegúrate de que tu `.gitignore` incluya:

```
# Entorno Virtual
venv/
env/
.venv

# Django
*.pyc
__pycache__/
db.sqlite3
*.log
*.pot

# Variables de Entorno
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Secretos
secrets/
*.key
*.pem
credentials.json
```

---

## Resumen de Fase 1

| Tarea | Estado | Verificación |
|-------|--------|-------------|
| Proyecto Jira creado |  | Acceso a tablero |
| Repositorio GitHub creado |  | URL funciona |
| Equipo agregado a Jira |  | Miembros visibles |
| Equipo agregado a GitHub |  | Collaborators visibles |
| Integración Jira-GitHub |  | Commit visible en ticket |
| Backlog inicial |  | Al menos 5 tickets |
| README.md |  | Archivo en repositorio |
| .gitignore |  | Archivo en repositorio |

---

**¡Fase 1 completada! El equipo está listo para comenzar el desarrollo.** 

Próximo paso: Leer [CONTRIBUTING.md](CONTRIBUTING.md) para la configuración inicial del código.
