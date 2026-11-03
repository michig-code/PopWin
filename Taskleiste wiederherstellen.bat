@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist popwin.pid (
    for /f %%p in (popwin.pid) do taskkill /f /fi "PID eq %%p" /fi "IMAGENAME eq pythonw.exe" >nul 2>&1
    del popwin.pid
)
".venv\Scripts\python.exe" core\taskbar_watchdog.py --restore
echo Die Windows-Taskleiste ist wieder da.
timeout /t 3 >nul
