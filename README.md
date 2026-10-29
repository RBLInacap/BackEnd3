# Sistema de Gestión de Resultados (SGR)

Aplicación Django de la Municipalidad de La Serena. El único proyecto ejecutable está en [`backend/`](backend/), con configuración, modelos, vistas, API, plantillas y archivos estáticos propios.

## Inicio rápido en Windows

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Configura `SECRET_KEY` en `backend/.env` con una clave generada para tu entorno. Luego:

```powershell
python manage.py migrate
python manage.py runserver
```

Abre <http://127.0.0.1:8000/>.

También puedes iniciar el sistema desde Windows ejecutando `iniciar_sistema.bat` en la raíz.

## Documentación y pruebas

- Instalación, configuración y despliegue: [`backend/README.md`](backend/README.md).
- Pruebas: desde `backend/`, ejecuta `python manage.py test sgr_app`.
- Variables privadas: usa `backend/.env.example` como referencia. No publiques `.env`, claves ni bases de datos.
