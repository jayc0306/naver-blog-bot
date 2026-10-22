@echo off
setlocal
cd /d "%~dp0"
set "NAVER_SETUP_ROOT=%LOCALAPPDATA%\NaverBlogBot"
if not defined LOCALAPPDATA set "NAVER_SETUP_ROOT=%USERPROFILE%\NaverBlogBot"
if not exist "%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" (
  py -3.12 -m venv "%NAVER_SETUP_ROOT%\venv"
  if errorlevel 1 (
    echo Python 3.12 is required. Install it from python.org with the Python launcher.
    pause
    exit /b 1
  )
)
"%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" -m pip --disable-pip-version-check install -r "%~dp0requirements-naver.txt"
if errorlevel 1 (
  echo Dependency installation failed. See the error above.
  pause
  exit /b 1
)
"%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" -m pip --disable-pip-version-check check
if errorlevel 1 (
  pause
  exit /b 1
)
echo Ready. Open NAVER_DRAFT_WINDOWS.md for login and temporary-save steps.
echo Microsoft Edge is used by default. No password is collected by this script.
pause
