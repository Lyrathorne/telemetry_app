@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1

python -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 exit /b 1

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests
if errorlevel 1 exit /b 1
set QT_QPA_PLATFORM=

python -m PyInstaller --noconfirm --clean RacingTelemetry.spec
if errorlevel 1 exit /b 1

echo.
echo Debug executable: %CD%\dist\RacingTelemetry-debug.exe
echo Release executable: %CD%\dist\RacingTelemetry.exe
exit /b 0
