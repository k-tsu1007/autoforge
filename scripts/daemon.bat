@echo off
REM 統合デーモン — APSchedulerで全ジョブを管理
cd /d "%~dp0\.."
python daemon.py >> logs\daemon.log 2>&1
