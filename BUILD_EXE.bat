@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo Building Moya Ishka 4.1.5.2 standalone Windows application...

if defined MOYA_BUILD_PYTHON (
  set "PY_EXE=%MOYA_BUILD_PYTHON%"
  set "PY_ARGS="
  goto have_python
)

where python >nul 2>nul
if not errorlevel 1 (
  set "PY_EXE=python"
  set "PY_ARGS="
  goto have_python
)

where py >nul 2>nul
if not errorlevel 1 goto use_py

goto no_python

:use_py
set "PY_EXE=py"
set "PY_ARGS=-3.11"

:have_python
"%PY_EXE%" %PY_ARGS% --version
if errorlevel 1 goto failed

"%PY_EXE%" %PY_ARGS% -c "import sys, struct; assert sys.version_info[:2] == (3, 11), f'Python 3.11 required, got {sys.version}'; assert struct.calcsize('P') == 8, '64-bit Python required'; print('Build interpreter:', sys.executable)"
if errorlevel 1 goto python_version_failed

"%PY_EXE%" %PY_ARGS% -m pip install --upgrade pip
if errorlevel 1 goto failed

"%PY_EXE%" %PY_ARGS% -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto failed

"%PY_EXE%" %PY_ARGS% -m pip check
if errorlevel 1 goto failed

"%PY_EXE%" %PY_ARGS% -c "import cryptography; from cryptography.exceptions import InvalidTag; from cryptography.hazmat.bindings import _rust; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; from cryptography.hazmat.primitives.kdf.scrypt import Scrypt; print('cryptography:', cryptography.__version__, cryptography.__file__); print('Rust binding:', _rust.__file__); print('AES-GCM and Scrypt imports: OK')"
if errorlevel 1 goto crypto_failed

"%PY_EXE%" %PY_ARGS% -m PyInstaller --noconfirm --clean --onefile --windowed --name "MoyaIshka" --icon app_icon.ico --version-file version_info.txt --additional-hooks-dir hooks ^
  --add-data "app_icon.ico;." ^
  --add-data "version_info.txt;source_project" ^
  --add-data "app_icon_256.png;." ^
  --add-data "app_icon_128.png;." ^
  --add-data "app_icon_64.png;." ^
  --add-data "app_icon_32.png;." ^
  --add-data "app_icon_16.png;." ^
  --add-data "main.py;source_project" ^
  --add-data "windows_control.py;source_project" ^
  --add-data "voice_assistant.py;source_project" ^
  --add-data "voice_manager.py;source_project" ^
  --add-data "auto_memory.py;source_project" ^
  --add-data "avatar_widget.py;source_project" ^
  --add-data "plugin_manager.py;source_project" ^
  --add-data "self_improvement.py;source_project" ^
  --add-data "updater.py;source_project" ^
  --add-data "updater_helper.py;source_project" ^
  --add-data "web_research.py;source_project" ^
  --add-data "cloud_sync.py;source_project" ^
  --add-data "google_drive_sync.py;source_project" ^
  --add-data "resource_governor.py;source_project" ^
  --add-data "sql;source_project/sql" ^
  --add-data "requirements.txt;source_project" ^
  --add-data "requirements_voice.txt;source_project" ^
  --add-data "START.bat;source_project" ^
  --add-data "BUILD_EXE.bat;source_project" ^
  --add-data "INSTALL_OPTIONAL_DOCS.bat;source_project" ^
  --add-data "INSTALL_VOICE.bat;source_project" ^
  --add-data "INSTALLER.iss;source_project" ^
  --add-data "README_RU.md;source_project" ^
  --add-data "SUPABASE_SETUP_RU.md;source_project" ^
  --add-data "GOOGLE_DRIVE_SETUP_RU.md;source_project" ^
  --add-data "CHANGELOG_RU.md;source_project" ^
  --add-data "LICENSE.txt;source_project" ^
  --add-data "THIRD_PARTY_NOTICES.md;source_project" ^
  --add-data "licenses/GPL-3.0.txt;source_project/licenses" ^
  --add-data "app_icon.ico;source_project" ^
  --add-data "tests;source_project/tests" ^
  --add-data "hooks;source_project/hooks" ^
  --add-data ".github;source_project/.github" ^
  --add-data "skills;source_project/skills" ^
  --collect-all pypdf --collect-all docx --collect-all speech_recognition ^
  --collect-all piper --collect-all onnxruntime ^
  --collect-all pyaudio --collect-all pyttsx3 --collect-all comtypes ^
  --collect-all cryptography --collect-all psutil ^
  --hidden-import=cryptography.exceptions ^
  --hidden-import=cryptography.hazmat.primitives.ciphers.aead ^
  --hidden-import=cryptography.hazmat.primitives.kdf.scrypt ^
  --hidden-import=cryptography.hazmat.bindings._rust ^
  --copy-metadata cryptography ^
  --hidden-import=pyaudio --hidden-import=pythoncom --hidden-import=pywintypes ^
  --hidden-import=win32com.client --hidden-import=pyttsx3.drivers.sapi5 ^
  --hidden-import=piper --hidden-import=onnxruntime.capi.onnxruntime_pybind11_state main.py
if errorlevel 1 goto failed

if not exist "dist\MoyaIshka.exe" goto failed

echo.
echo Checking the frozen executable crypto imports (90-second limit)...
"%PY_EXE%" %PY_ARGS% -c "import subprocess, sys; result = subprocess.run([r'dist\MoyaIshka.exe', '--self-test-imports'], timeout=90); sys.exit(result.returncode)"
if errorlevel 1 goto crypto_bundle_failed

echo.
echo BUILD COMPLETE: dist\MoyaIshka.exe
echo The standalone EXE includes Python and cryptography. No Python or Node.js installation is required on the user's PC.
if not defined GITHUB_ACTIONS pause
endlocal & exit /b 0

:python_version_failed
echo.
echo Python 3.11 x64 is required for this reproducible build.
goto failed_exit

:crypto_failed
echo.
echo CRYPTOGRAPHY PREFLIGHT FAILED. The build cannot continue.
goto failed_exit

:crypto_bundle_failed
echo.
echo CRYPTOGRAPHY BUNDLE SMOKE TEST FAILED. The EXE must not be distributed.
goto failed_exit

:no_python
echo.
echo Python 3.11 x64 was not found on this build computer.
goto failed_exit

:failed
echo.
echo EXE build failed. Check the first error above and confirm internet access.

:failed_exit
if not defined GITHUB_ACTIONS pause
endlocal & exit /b 1
