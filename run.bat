@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo JARVIS is not installed yet - running the installer first...
    call install.bat
    exit /b
)
start "" ".venv\Scripts\pythonw.exe" main.py
