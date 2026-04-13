@echo off
cd /d C:\Users\Tsubasa\autoforge
python -m tools.run_daemon --instance ai_bento >> instances\ai_bento\daemon.log 2>&1
