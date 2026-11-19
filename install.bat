@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title J.A.R.V.I.S. Installer
color 06
echo.
echo   =====================================================
echo                J . A . R . V . I . S .
echo         Just A Rather Very Intelligent System
echo   =====================================================
echo.

rem ---------------------------------------------------------------- Python
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [!] Python 3.12 was not found.
    echo     Install it from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
    echo     or run:  winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('%PY% --version') do set "PYVER=%%v"
echo [*] Using Python %PYVER%  ^(%PY%^)

rem ------------------------------------------------------- virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set "VPY=.venv\Scripts\python.exe"

echo [*] Installing Python packages ^(this can take a few minutes^)...
"%VPY%" -m pip install --upgrade pip wheel
if errorlevel 1 goto :fail
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

rem ------------------------------------------------------------------ Ollama
set "OLLAMA="
where ollama >nul 2>&1 && set "OLLAMA=ollama"
if not defined OLLAMA if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA (
    echo.
    echo [!] Ollama ^(the local AI engine^) is not installed.
    choice /C YN /M "    Install Ollama now using winget"
    if not errorlevel 2 (
        winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
        if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    )
)
if not defined OLLAMA (
    echo [!] Skipping model download. Install Ollama from https://ollama.com/download then run:
    echo         ollama pull llama3.1:8b
    goto :whisper
)

echo.
echo [*] Choose a language model to download:
echo       1^) llama3.1:8b   - recommended, 4.9 GB, needs ~8 GB RAM, supports tool calling
echo       2^) qwen2.5:7b    - excellent alternative, 4.7 GB, supports tool calling
echo       3^) llama3.2:3b   - for low-end PCs, 2.0 GB
echo       4^) skip
choice /C 1234 /N /M "    Select 1-4: "
set "MODEL="
if errorlevel 4 goto :whisper
if errorlevel 3 set "MODEL=llama3.2:3b"
if not defined MODEL if errorlevel 2 set "MODEL=qwen2.5:7b"
if not defined MODEL set "MODEL=llama3.1:8b"
echo [*] Pulling %MODEL% ...
"%OLLAMA%" pull %MODEL%
if errorlevel 1 (
    echo [!] Model download failed - make sure the Ollama app is running, then run: ollama pull %MODEL%
) else (
    "%VPY%" -c "from jarvis.config import Config; c=Config.load(); c.model='%MODEL%'; c.save(); print('[*] JARVIS configured to use', c.model)"
)

:whisper
echo.
echo [*] Pre-downloading the speech recognition model ^(base.en, ~150 MB^)...
"%VPY%" -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8'); print('[*] Speech model ready')"

rem ------------------------------------------------------------ shortcut
echo [*] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\JARVIS.lnk');" ^
  "$s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0main.py\"'; $s.WorkingDirectory='%~dp0';" ^
  "$s.Description='JARVIS Personal AI'; $s.Save()" >nul 2>&1

echo.
echo   =====================================================
echo     Installation complete.  Start JARVIS with run.bat
echo     or the JARVIS shortcut on your desktop.
echo   =====================================================
echo.
choice /C YN /M "Launch JARVIS now"
if not errorlevel 2 start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:fail
echo.
echo [X] Installation failed. Scroll up for the error message.
echo     Common fixes: use 64-bit Python 3.12, check your internet connection, run again.
pause
exit /b 1
