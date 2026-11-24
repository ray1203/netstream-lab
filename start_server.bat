@echo off
rem NetStream Lab server launcher. Double-click, or drag a video folder onto this file.
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
title NetStream Lab server

if exist "%~dp0source\pc\netstream" (cd /d "%~dp0source\pc") else (cd /d "%~dp0pc")

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or later from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo Python 3.10 or later is required.
    python --version
    pause
    exit /b 1
)

python -c "import sys; from netstream.server.transcoder import find_tool; sys.exit(0 if find_tool('ffmpeg') and find_tool('ffprobe') else 1)"
if errorlevel 1 (
    echo ffmpeg was not found.
    choice /c YN /m "Install it now with winget (Gyan.FFmpeg)"
    if errorlevel 2 (
        echo Install ffmpeg and run this file again.
        pause
        exit /b 1
    )
    winget install --id Gyan.FFmpeg -e
)

set "MEDIA=%~1"
if not defined MEDIA (
    echo.
    echo Type the folder that has the videos and press Enter.
    echo Just press Enter to use %USERPROFILE%\Videos
    set /p "MEDIA=Video folder: "
)
if not defined MEDIA set "MEDIA=%USERPROFILE%\Videos"
set MEDIA=%MEDIA:"=%
if not exist "%MEDIA%" (
    echo Folder not found: %MEDIA%
    pause
    exit /b 1
)

echo.
echo Starting the server. The dashboard opens in the browser in a few seconds.
echo If Windows shows a firewall popup, allow it. Close this window to stop the server.
echo.
start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start http://127.0.0.1:9080"
python -m netstream.server --media "%MEDIA%"
pause
