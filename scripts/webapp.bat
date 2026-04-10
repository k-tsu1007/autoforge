@echo off
REM FastAPI Web管理画面 — autoforge v2
cd /d "%~dp0\.."
python -c "import fastapi, jinja2" 2>nul || python -m pip install fastapi uvicorn python-multipart jinja2 pyyaml -q
python -m tools.run_webapp --instance fuku_ai_sns >> logs\webapp.log 2>&1
