@echo off
setlocal EnableExtensions

cd /d "%~dp0" || (
    echo Build failed: could not switch to script directory.
    exit /b 1
)

where powershell >nul 2>nul
if errorlevel 1 (
    echo Build failed: Windows PowerShell was not found.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
exit /b %ERRORLEVEL%
