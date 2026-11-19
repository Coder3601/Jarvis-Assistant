@echo off
rem Runs JARVIS with a console window so you can see log output and errors.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py
pause
