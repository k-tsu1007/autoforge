@echo off
REM 日次パイプライン — JST 18:00に実行
cd /d "%~dp0\.."

REM .envファイルから環境変数を読み込み
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if not "%%a"=="" if not "%%a:~0,1"=="#" set "%%a=%%b"
)

set USE_CLAUDE_CLI=1
python run.py >> logs\daily_pipeline.log 2>&1
exit /b %errorlevel%
