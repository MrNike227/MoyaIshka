@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0ISH.exe" (
  start "" "%~dp0ISH.exe"
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if errorlevel 1 goto no_python
set "PY=python"
goto run_source
:use_py
set "PY=py -3"
:run_source
%PY% -c "import cryptography, cffi, _cffi_backend" >nul 2>nul
if errorlevel 1 (
    echo Required cryptography/cffi packages are missing. Installing them...
    %PY% -m pip install --upgrade "cryptography>=42.0" "cffi>=2.0.0"
    if errorlevel 1 goto crypto_install_failed
)
%PY% main.py
set "APP_RC=%ERRORLEVEL%"
if not "%APP_RC%"=="0" (
    echo.
    echo The application exited with an error.
    echo Check README_RU.md for setup instructions.
    pause
)
endlocal & exit /b %APP_RC%
:crypto_install_failed
echo.
echo Could not install cryptography. Check your internet connection and Python pip.
echo Manual command: %PY% -m pip install "cryptography>=42.0" "cffi>=2.0.0"
pause
endlocal & exit /b 1
:no_python
echo Python was not found. Use the standalone ISH.exe or install Python for source mode.
pause
endlocal & exit /b 1
