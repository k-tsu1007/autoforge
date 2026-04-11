@echo off
REM 自動同期スクリプト — 5分ごとに git pull + health.json をpush
REM .pyファイルに変更があればrestart.flagを置いてデーモンにグレースフルシャットダウンを依頼
REM （デーモンは実行中ジョブが終わってから1分以内に終了 → Task Schedulerが自動再起動）
cd /d "%~dp0\.."

REM pullで最新化（変更があったか記録）
git fetch --quiet 2>nul
git diff --name-only HEAD origin/main 2>nul | findstr "\.py" >nul 2>&1
set PY_CHANGED=%ERRORLEVEL%

git pull --rebase 2>nul

REM .pyファイルが更新された場合はrestart.flagを置く（強制終了はしない）
if %PY_CHANGED%==0 (
    echo [auto_sync] .py変更検出 ^— restart.flag を置きグレースフルシャットダウンを依頼
    echo. > instances\fuku_ai_sns\data\restart.flag
)

REM health.jsonに変更があればpush
git add instances\fuku_ai_sns\data\health.json 2>nul
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "auto: health update" 2>nul
    git push 2>nul
)
exit /b 0
