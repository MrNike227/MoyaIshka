@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if errorlevel 1 goto no_python
set "PY=python"
goto install
:use_py
set "PY=py -3"
:install
%PY% -m pip install --upgrade -r requirements_voice.txt
if errorlevel 1 goto failed
echo.
echo Voice components installed. Restart the application.
pause
endlocal & exit /b 0
:failed
echo.
echo Voice component installation failed.
echo PyAudio may require 64-bit Python 3.10-3.12 and a compatible wheel.
pause
endlocal & exit /b 1
:no_python
echo Python 3 was not found. Install Python 3.10 or newer.
pause
endlocal & exit /b 1
