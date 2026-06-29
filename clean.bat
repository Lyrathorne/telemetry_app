@echo off
setlocal
cd /d "%~dp0"

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist deployment rmdir /s /q deployment

for /d /r %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d"
)

if exist .pytest_cache rmdir /s /q .pytest_cache

echo Build artifacts removed.
