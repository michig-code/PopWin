@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem PopWin aus dem Projektordner starten, auf jedem PC. Fehlt etwas, richtet sich PopWin hier selbst ein.

rem Eine mitkopierte .venv zeigt auf das Python eines anderen PCs und laeuft hier nicht: dann neu anlegen.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1 || (
        echo Die Python-Umgebung stammt von einem anderen PC und wird neu angelegt ...
        rmdir /s /q .venv
    )
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo PopWin wird zum ersten Mal eingerichtet. Das dauert ein paar Minuten ...
    call :make_venv || goto :no_python
)
rem Neue Pakete in requirements.txt kommen beim naechsten Start von selbst dazu.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1 || (
    echo Pakete werden eingerichtet ...
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :no_install
    copy /y requirements.txt ".venv\requirements.installed" >nul
)
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:make_venv
rem 3.12 zuerst: Fuer sie gibt es alle Pakete fertig gebaut.
py -3.12 -m venv .venv >nul 2>&1 && exit /b 0
py -3.13 -m venv .venv >nul 2>&1 && exit /b 0
py -3 -m venv .venv >nul 2>&1 && exit /b 0
python -m venv .venv >nul 2>&1 && exit /b 0
exit /b 1

:no_python
echo Python wurde nicht gefunden. Bitte Python 3.12 von python.org installieren, mit Haken bei "Add python.exe to PATH".
pause
exit /b 1

:no_install
echo Die Installation ist fehlgeschlagen. Bitte die Internetverbindung pruefen und die Datei noch einmal starten.
pause
exit /b 1