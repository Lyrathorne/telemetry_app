@echo off
setlocal EnableExtensions

cd /d "%~dp0" || (
    echo Build failed: could not switch to script directory.
    exit /b 1
)

set "APP_NAME=RacingTelemetry"
set "DEBUG_EXE=%CD%\dist\%APP_NAME%-debug.exe"
set "RELEASE_EXE=%CD%\dist\%APP_NAME%.exe"
set "BUILD_DIR=build_new"
set "DIST_DIR=dist_new"
set "FAIL_STAGE=initialization"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
) else if exist ".venv.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\.venv.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Build failed: Python was not found on PATH and no project virtual environment was found.
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

echo Using Python:
"%PYTHON_EXE%" -c "import sys; print(sys.executable)"
if errorlevel 1 (
    echo Build failed: selected Python could not start.
    exit /b 1
)

set "FAIL_STAGE=dependency check"
"%PYTHON_EXE%" -c "import PySide6, numpy, pyqtgraph, PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo Missing build dependencies detected. Installing project requirements...
    "%PYTHON_EXE%" -m pip install -r requirements.txt -r requirements-dev.txt
    if errorlevel 1 goto :fail
)

set "FAIL_STAGE=PyInstaller check"
"%PYTHON_EXE%" -m PyInstaller --version
if errorlevel 1 goto :fail

set "FAIL_STAGE=cleanup of temporary build folders"
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if errorlevel 1 goto :fail
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if errorlevel 1 goto :fail

set "FAIL_STAGE=test run"
set QT_QPA_PLATFORM=offscreen
"%PYTHON_EXE%" -m unittest discover -s tests
set "TEST_RESULT=%ERRORLEVEL%"
set QT_QPA_PLATFORM=
if not "%TEST_RESULT%"=="0" (
    set "ERRORLEVEL=%TEST_RESULT%"
    goto :fail
)

set "FAIL_STAGE=PyInstaller build"
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --workpath "%BUILD_DIR%" --distpath "%DIST_DIR%" RacingTelemetry.spec
if errorlevel 1 goto :fail

set "FAIL_STAGE=executable verification"
if not exist "%DIST_DIR%\%APP_NAME%-debug.exe" (
    echo Build finished, but debug executable was not found: %CD%\%DIST_DIR%\%APP_NAME%-debug.exe
    echo Actual output directory contents:
    if exist "%DIST_DIR%" (dir "%DIST_DIR%") else (echo %DIST_DIR% does not exist.)
    exit /b 1
)
if not exist "%DIST_DIR%\%APP_NAME%.exe" (
    echo Build finished, but release executable was not found: %CD%\%DIST_DIR%\%APP_NAME%.exe
    echo Actual output directory contents:
    if exist "%DIST_DIR%" (dir "%DIST_DIR%") else (echo %DIST_DIR% does not exist.)
    exit /b 1
)

set "FAIL_STAGE=replace dist folder"
if exist dist rmdir /s /q dist
if errorlevel 1 (
    echo Build produced new executables, but the existing dist folder could not be replaced.
    echo Close any running executable and try again.
    echo New debug executable: %CD%\%DIST_DIR%\%APP_NAME%-debug.exe
    echo New release executable: %CD%\%DIST_DIR%\%APP_NAME%.exe
    exit /b 1
)

ren "%DIST_DIR%" dist
if errorlevel 1 goto :fail

if exist build rmdir /s /q build
if exist "%BUILD_DIR%" ren "%BUILD_DIR%" build

set "FAIL_STAGE=final executable verification"
if not exist "%DEBUG_EXE%" goto :fail_missing_final
if not exist "%RELEASE_EXE%" goto :fail_missing_final

echo.
echo Build succeeded.
echo Debug executable: %DEBUG_EXE%
echo Release executable: %RELEASE_EXE%
exit /b 0

:fail_missing_final
echo Build failed: final executable was not found after replacing dist.
echo Expected debug executable: %DEBUG_EXE%
echo Expected release executable: %RELEASE_EXE%
echo Actual dist contents:
if exist dist (dir dist) else (echo dist does not exist.)
exit /b 1

:fail
echo.
echo Build failed during: %FAIL_STAGE%
echo Exit code: %ERRORLEVEL%
echo Expected debug executable: %DEBUG_EXE%
echo Expected release executable: %RELEASE_EXE%
echo Existing dist contents:
if exist dist (dir dist) else (echo dist does not exist.)
echo Temporary output contents:
if exist "%DIST_DIR%" (dir "%DIST_DIR%") else (echo %DIST_DIR% does not exist.)
exit /b %ERRORLEVEL%
