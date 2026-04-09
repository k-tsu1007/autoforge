@echo off
REM FastAPI Web管理画面 — 0.0.0.0:8502 で起動
cd /d "%~dp0\.."
python -m webapp.server >> logs\webapp.log 2>&1
