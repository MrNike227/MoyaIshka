@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0MoyaIshka.exe" (
  start "" "%~dp0MoyaIshka.exe"
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if errorlevel 1 goto no_python
python main.py
set "APP_RC=%ERRORLEVEL%"
goto finished
:use_py
py -3 main.py
set "APP_RC=%ERRORLEVEL%"
:finished
if not "%APP_RC%"=="0" (
    echo.
    echo The application exited with an error.
    echo Check README_RU.md for setup instructions.
    pause
)
endlocal & exit /b %APP_RC%
:no_python
echo Python was not found. Use the standalone MoyaIshka.exe or install Python for source mode.
pause
endlocal & exit /b 1
