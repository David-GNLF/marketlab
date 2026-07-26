@echo off
REM Lanceur Planificateur de taches : publication quotidienne du site.
REM Plan B local — normalement assuree par GitHub Actions (publication.yml) ;
REM n'activer cette tache que si Actions est indisponible (facturation...).
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set RACINE=%~dp0..
set PY=%RACINE%\.venv\Scripts\python.exe
set JOURNAL=%RACINE%\data_local\logs\publication.log
if not exist "%RACINE%\data_local\logs" mkdir "%RACINE%\data_local\logs"
echo ============ %DATE% %TIME% ============>> "%JOURNAL%"
if not exist "%PY%" (
  echo ERREUR : venv introuvable>> "%JOURNAL%"
  endlocal
  exit /b 2
)
"%PY%" "%RACINE%\scripts\publier.py" >> "%JOURNAL%" 2>&1
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" echo [ECHEC] code de sortie %CODE%>> "%JOURNAL%"
endlocal & exit /b %CODE%
