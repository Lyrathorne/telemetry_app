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

set BUILD_DIR=build_new
set DIST_DIR=dist_new

if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"

set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests
if errorlevel 1 (
    set QT_QPA_PLATFORM=
    echo.
    echo Tests failed. Existing dist folder was not changed.
    exit /b 1
)
set QT_QPA_PLATFORM=

python -m PyInstaller --noconfirm --clean --workpath "%BUILD_DIR%" --distpath "%DIST_DIR%" RacingTelemetry.spec
if errorlevel 1 (
    echo.
    echo PyInstaller failed. Existing dist folder was not changed.
    exit /b 1
)

if exist dist rmdir /s /q dist
if errorlevel 1 (
    echo.
    echo Could not replace the existing dist folder. Close any running executable and try again.
    echo New build is available in: %CD%\%DIST_DIR%
    exit /b 1
)

ren "%DIST_DIR%" dist
if errorlevel 1 exit /b 1

if exist build rmdir /s /q build
if exist "%BUILD_DIR%" ren "%BUILD_DIR%" build

echo.
echo Debug executable: %CD%\dist\RacingTelemetry-debug.exe
echo Release executable: %CD%\dist\RacingTelemetry.exe
exit /b 0
