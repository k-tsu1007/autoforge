@echo off
cd /d C:\Users\Tsubasa\autoforge
git pull
echo.
echo ===== fuku_ai_sns の Cookie 再取得 =====
echo ブラウザが開くので自動ログインします
echo 途中で止まったら手動で完了させてください
echo.
python -m tools.refresh_x_cookies --instance fuku_ai_sns
echo.
if %errorlevel% == 0 (
    echo [OK] Cookie 取得成功
) else (
    echo [NG] 失敗しました
)
pause
