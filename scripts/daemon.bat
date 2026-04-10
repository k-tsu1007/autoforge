@echo off
REM 統合デーモン — autoforge v2
cd /d "%~dp0\.."
python -m tools.run_daemon --instance fuku_ai_sns >> logs\daemon.log 2>&1
