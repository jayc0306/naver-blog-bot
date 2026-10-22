@echo off
setlocal
cd /d "%~dp0"
set "NAVER_SETUP_ROOT=%LOCALAPPDATA%\NaverBlogBot"
if not defined LOCALAPPDATA set "NAVER_SETUP_ROOT=%USERPROFILE%\NaverBlogBot"
if exist "%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" goto validate_environment

py -3.12 --version 2>nul | findstr /b /c:"Python 3.12." >nul
if not errorlevel 1 goto create_with_launcher

python --version 2>nul | findstr /b /c:"Python 3.12." >nul
if not errorlevel 1 goto create_with_python

where py >nul 2>&1
if errorlevel 1 goto missing_python
echo Python 3.12 is missing. Trying the Python install manager...
py install 3.12
py -3.12 --version 2>nul | findstr /b /c:"Python 3.12." >nul
if errorlevel 1 goto missing_python

:create_with_launcher
py -3.12 -m venv "%NAVER_SETUP_ROOT%\venv"
if errorlevel 1 goto environment_failed
goto validate_environment

:create_with_python
python -m venv "%NAVER_SETUP_ROOT%\venv"
if errorlevel 1 goto environment_failed

:validate_environment
if not exist "%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" goto environment_failed
"%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" --version 2>nul | findstr /b /c:"Python 3.12." >nul
if errorlevel 1 goto environment_failed
"%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" -m pip --disable-pip-version-check install -r "%~dp0requirements-naver.txt"
if errorlevel 1 goto dependency_failed
"%NAVER_SETUP_ROOT%\venv\Scripts\python.exe" -m pip --disable-pip-version-check check
if errorlevel 1 goto dependency_failed
echo Ready. Open NAVER_DRAFT_WINDOWS.md for login and temporary-save steps.
echo Microsoft Edge is used by default. No password is collected by this script.
pause
exit /b 0

:missing_python
echo Python 3.12 could not be found or installed.
echo In Windows Terminal, run: py install 3.12
echo Then check: py -3.12 --version
echo If the install command is unavailable, install Python 3.12 from python.org.
echo Run setup_naver.bat again after Python is installed.
pause
exit /b 1

:environment_failed
echo The Python environment could not be created or is not Python 3.12.
echo Dependency installation was not started.
echo Check the error above and available disk space.
echo If an incomplete environment already exists, rename only the venv folder
echo under LOCALAPPDATA\NaverBlogBot and run setup_naver.bat again.
echo Keep browser-profile and draft folders.
pause
exit /b 1

:dependency_failed
echo Dependency installation or validation failed. See the error above.
pause
exit /b 1
