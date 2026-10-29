@echo off
chcp 65001 > nul
cls
echo ===============================================================================
echo        SISTEMA DE GESTION DE RESULTADOS (MATRIZ SGR) - I. MUNICIPALIDAD DE LA SERENA
echo ===============================================================================
echo.
echo Entorno local: http://127.0.0.1:8000
echo.

cd /d "%~dp0backend"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
) else (
    set "PYTHON_CMD=python"
)

echo [Paso 1/3] Aplicando migraciones...
%PYTHON_CMD% manage.py migrate --noinput
if errorlevel 1 (
    echo [ERROR] No se pudieron aplicar las migraciones. Revisa backend\.env y la base de datos.
    goto error
)

echo [Paso 2/3] Verificando la configuracion...
%PYTHON_CMD% manage.py check
if errorlevel 1 goto error

echo [Paso 3/3] Iniciando servidor web...
echo.
echo  - Plataforma Web: http://127.0.0.1:8000
echo.
echo Presione [Ctrl + C] para detener el servidor.
echo ===============================================================================
echo.

%PYTHON_CMD% manage.py runserver 127.0.0.1:8000
goto fin

:error
pause

:fin
pause
