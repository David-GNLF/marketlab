@echo off
REM Lanceur pour le Planificateur de taches Windows : historisation des scores.
REM Utilise le venv du projet (.venv) : le site-packages utilisateur n'est pas
REM garanti dans le contexte d'une tache planifiee.
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set RACINE=%~dp0..
set PY=%RACINE%\.venv\Scripts\python.exe
set JOURNAL=%RACINE%\data_local\logs\historiser.log
if not exist "%RACINE%\data_local\logs" mkdir "%RACINE%\data_local\logs"
echo ============ %DATE% %TIME% ============>> "%JOURNAL%"
if not exist "%PY%" (
  echo ERREUR : venv introuvable. Creer avec :>> "%JOURNAL%"
  echo   C:\Python314\python.exe -m venv "%RACINE%\.venv">> "%JOURNAL%"
  echo   "%RACINE%\.venv\Scripts\pip.exe" install -r "%RACINE%\requirements.txt">> "%JOURNAL%"
  endlocal
  exit /b 2
)
"%PY%" "%RACINE%\scripts\historiser.py" >> "%JOURNAL%" 2>&1
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" echo [ECHEC] code de sortie %CODE%>> "%JOURNAL%"
endlocal & exit /b %CODE%
