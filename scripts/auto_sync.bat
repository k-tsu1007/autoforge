@echo off
REM 自動同期スクリプト — 5分ごとに git pull + health.json をpush
cd /d "%~dp0\.."

REM まずpullで最新化
git pull --rebase 2>nul

REM health.jsonに変更があればpush
git add instances\fuku_ai_sns\data\health.json 2>nul
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "auto: health update" 2>nul
    git push 2>nul
)
exit /b 0
