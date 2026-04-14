@echo off
cd /d C:\Users\Tsubasa\autoforge

echo [deploy] git pull...
git pull origin main
if %errorlevel% neq 0 (
    echo [deploy] git pull failed
    exit /b 1
)

call restart.bat
