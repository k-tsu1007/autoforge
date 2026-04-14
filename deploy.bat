@echo off
cd /d C:\Users\Tsubasa\autoforge

echo [deploy] git pull...
git fetch origin main
git checkout --theirs -- instances/
git add instances/ >nul 2>&1
git pull origin main
if %errorlevel% neq 0 (
    echo [deploy] git pull failed — trying with stash...
    git stash
    git pull origin main
    git stash pop
)

call restart.bat
