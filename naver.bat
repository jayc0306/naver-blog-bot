@echo off
setlocal
cd /d "%~dp0"
set "NAVER_SETUP_ROOT=%LOCALAPPDATA%\NaverBlogBot"
if not defined LOCALAPPDATA set "NAVER_SETUP_ROOT=%USERPROFILE%\NaverBlogBot"
if not exist "%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" (
  echo Run setup_naver.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
if "%~1"=="" (
  "%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" "%~dp0naver_draft.py" --help
  pause
  exit /b 0
)
"%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" "%~dp0naver_draft.py" %*
exit /b %ERRORLEVEL%
