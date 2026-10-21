@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Run setup.bat first.
  pause
  exit /b 1
)
if not exist .env if exist .env.txt ren .env.txt .env
if not exist .env (
  echo .env file not found. Run setup.bat again.
  pause
  exit /b 1
)
set PYTHONUTF8=1
call .venv\Scripts\activate.bat
echo Starting bot... Keep this window open. Close it to stop the bot.
echo.
python bot.py
echo.
echo Bot stopped. If you see an error above, take a screenshot and send it to Claude.
pause
