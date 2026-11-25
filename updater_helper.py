# -*- coding: utf-8 -*-
"""Windows updater helpers.

A separate PowerShell process performs the EXE replacement after the GUI exits.
The helper is written to the system temporary directory, so the running EXE is
never asked to replace itself while it is locked by Windows.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def user_install_dir() -> Path:
    """Default per-user install directory (used only when the user has not chosen one)."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Programs" / "MoyaIshka"


def resolve_install_dir(configured: str | Path | None = None, running_exe: str | Path | None = None) -> Path:
    """Resolve the persistent install folder.

    Priority: explicitly saved folder, current frozen EXE folder, then per-user default.
    The folder is deliberately independent from APPDATA, which is reserved for user data.
    """
    if configured:
        value = os.path.expandvars(str(configured).strip())
        if value:
            return Path(value).expanduser().resolve()
    if running_exe:
        exe = Path(running_exe).expanduser().resolve()
        if exe.suffix.lower() == ".exe" and exe.parent.name.casefold() != "update_staging":
            return exe.parent
    return user_install_dir().resolve()


def ensure_install_dir_writable(directory: str | Path) -> Path:
    """Create and verify a selected installation directory before staging an install."""
    path = Path(directory).expanduser().resolve()
    if path == Path(path.anchor):
        raise ValueError(r"Нельзя устанавливать приложение непосредственно в корень диска. Выбери отдельную папку, например C:\MoyaIshka.")
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".moya_write_test_{os.getpid()}"
    try:
        probe.write_text("ok", encoding="ascii")
    finally:
        probe.unlink(missing_ok=True)
    return path


def install_from_source(staged_path: str | Path, install_dir: str | Path | None = None) -> Path:
    """Install the downloaded standalone EXE when the app was run from source."""
    staged = Path(staged_path).resolve()
    if not staged.is_file() or staged.suffix.lower() != ".exe":
        raise FileNotFoundError(f"Проверенный EXE не найден: {staged}")

    destination_dir = Path(install_dir) if install_dir else user_install_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / "MoyaIshka.exe"
    temporary = destination_dir / "MoyaIshka.new.exe"

    # Copy to the destination volume first, then atomically rename into place.
    shutil.copy2(staged, temporary)
    if temporary.stat().st_size != staged.stat().st_size:
        temporary.unlink(missing_ok=True)
        raise IOError("Скопированный EXE имеет неверный размер.")
    os.replace(temporary, target)
    return target


def _powershell_script() -> str:
    return r'''param(
    [Parameter(Mandatory=$true)][string]$StagedPath,
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [Parameter(Mandatory=$true)][string]$LogPath
)
$ErrorActionPreference = 'Stop'
$backup = Join-Path (Split-Path -Parent $TargetPath) "MoyaIshka.previous.exe"
$tempTarget = "$TargetPath.new.exe"
function Write-UpdateLog([string]$Message) {
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Add-Content -LiteralPath $LogPath -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) -Encoding UTF8
    } catch {}
}
try {
    # Wait until the old GUI process releases its EXE and database files.
    try {
        $old = [System.Diagnostics.Process]::GetProcessById($ParentPid)
        if (-not $old.WaitForExit(120000)) { throw 'Старая ИИшка не закрылась за 120 секунд.' }
    } catch [System.ArgumentException] {
        # The old process has already exited.
    }
    Start-Sleep -Milliseconds 700

    if (-not (Test-Path -LiteralPath $StagedPath -PathType Leaf)) { throw "Не найден файл обновления: $StagedPath" }
    $actualHash = (Get-FileHash -LiteralPath $StagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) { throw 'SHA-256 изменился после загрузки. Замена отменена.' }

    $targetDir = Split-Path -Parent $TargetPath
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    Copy-Item -LiteralPath $StagedPath -Destination $tempTarget -Force

    if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
        # File.Replace is atomic when source and target are on the same volume.
        try {
            [System.IO.File]::Replace($tempTarget, $TargetPath, $backup, $true)
        } catch {
            if (Test-Path -LiteralPath $TargetPath) { Move-Item -LiteralPath $TargetPath -Destination $backup -Force }
            Move-Item -LiteralPath $tempTarget -Destination $TargetPath -Force
        }
    } else {
        Move-Item -LiteralPath $tempTarget -Destination $TargetPath -Force
    }

    $newProcess = Start-Process -FilePath $TargetPath -WorkingDirectory $targetDir -PassThru
    Start-Sleep -Seconds 4
    $newProcess.Refresh()
    if ($newProcess.HasExited -and $newProcess.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $backup) {
            Copy-Item -LiteralPath $backup -Destination $TargetPath -Force
            Start-Process -FilePath $TargetPath -WorkingDirectory $targetDir | Out-Null
        }
        throw "Новая версия завершилась с кодом $($newProcess.ExitCode). Восстановлена предыдущая версия."
    }

    Remove-Item -LiteralPath $StagedPath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "Обновление установлено: $TargetPath"
    exit 0
} catch {
    Write-UpdateLog ("ОШИБКА: " + $_.Exception.Message)
    if (Test-Path -LiteralPath $tempTarget) { Remove-Item -LiteralPath $tempTarget -Force -ErrorAction SilentlyContinue }
    exit 1
} finally {
    try { if ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath)) { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue } } catch {}
}
'''


def launch_windows_replacement(
    staged_path: str | Path,
    target_path: str | Path,
    parent_pid: int,
    log_path: str | Path,
) -> Path:
    """Start a detached PowerShell updater which waits for this process to exit."""
    if os.name != "nt":
        raise OSError("Автоматическая замена EXE поддерживается только в Windows.")

    staged = Path(staged_path).resolve()
    target = Path(target_path).resolve()
    if not staged.is_file():
        raise FileNotFoundError(f"Файл обновления не найден: {staged}")
    if target.suffix.lower() != ".exe":
        raise ValueError("Путь установленного приложения должен оканчиваться на .exe")

    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
    helper_fd, helper_name = tempfile.mkstemp(prefix="MoyaIshka_Updater_", suffix=".ps1")
    helper = Path(helper_name)
    try:
        with os.fdopen(helper_fd, "w", encoding="utf-8-sig", newline="\r\n") as stream:
            stream.write(_powershell_script())

        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise FileNotFoundError("Windows PowerShell не найден. Он обычно входит в Windows 10.")

        command = [
            powershell,
            "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(helper),
            str(staged), str(target), str(int(parent_pid)), digest, str(Path(log_path).resolve()),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(command, close_fds=True, creationflags=creation_flags)
        return helper
    except Exception:
        helper.unlink(missing_ok=True)
        raise


# Backward-compatible entry point for older packaged builds. New builds use
# launch_windows_replacement() so the helper is not another instance of the EXE.
def apply_update(staged_path: str, target_path: str, process_id: str) -> int:
    try:
        launch_windows_replacement(staged_path, target_path, int(process_id), user_install_dir() / "update.log")
        return 0
    except Exception:
        return 1
