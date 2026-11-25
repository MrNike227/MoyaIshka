@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if errorlevel 1 goto no_python
python -m pip install --upgrade pypdf python-docx
goto result
:use_py
py -3 -m pip install --upgrade pypdf python-docx
:result
if errorlevel 1 goto failed
echo Optional PDF and DOCX support installed.
pause
endlocal & exit /b 0
:failed
echo Installation of optional document libraries failed.
pause
endlocal & exit /b 1
:no_python
echo Python 3 was not found. Install Python 3.10 or newer.
pause
endlocal & exit /b 1
