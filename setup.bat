@echo off
cd /d "%~dp0"
echo.
echo [1/3] Creating Python environment...
python -m venv .venv
if errorlevel 1 (
  echo.
  echo ERROR: Python not found. Install Python from python.org and CHECK "Add python.exe to PATH".
  pause
  exit /b 1
)
echo [2/3] Installing packages... (1-2 min)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: Install failed. Copy the messages above and send them to Claude.
  pause
  exit /b 1
)
echo [3/3] Opening key file...
if not exist .env copy .env.example .env >nul
start notepad .env
echo.
echo DONE. Paste your keys into Notepad, save (Ctrl+S), then double-click start.bat
pause
