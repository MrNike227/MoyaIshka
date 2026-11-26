# -*- coding: utf-8 -*-
"""Windows updater helpers.

A separate PowerShell process performs the EXE replacement after the GUI exits.
The helper is written to the system temporary directory, so the running EXE is
never asked to replace itself while it is locked by Windows.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


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


def sha256_file(path: str | Path) -> str:
    """Calculate SHA-256 without loading the whole EXE into RAM."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _powershell_script() -> str:
    """Return a transactional updater script with a startup-confirmation handshake.

    The helper waits for the old process, verifies the staged file, makes a backup,
    replaces the target, launches it and waits until the new app confirms its own
    version/path/hash. Any failure triggers rollback and a restart of the old EXE.
    """
    return r'''param(
    [Parameter(Mandatory=$true)][string]$StagedPath,
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [Parameter(Mandatory=$true)][string]$ExpectedVersion,
    [Parameter(Mandatory=$true)][string]$LogPath,
    [Parameter(Mandatory=$true)][string]$ReceiptPath
)
$ErrorActionPreference = 'Stop'
$TargetPath = [System.IO.Path]::GetFullPath($TargetPath)
$StagedPath = [System.IO.Path]::GetFullPath($StagedPath)
$targetDir = Split-Path -Parent $TargetPath
$backup = Join-Path $targetDir 'MoyaIshka.previous.exe'
$oldSwap = Join-Path $targetDir 'MoyaIshka.rollback.exe'
$tempTarget = Join-Path $targetDir 'MoyaIshka.installing.exe'
$newProcess = $null
$hadPrevious = Test-Path -LiteralPath $TargetPath -PathType Leaf
$previousHash = $null
$backupReady = $false

function Write-UpdateLog([string]$Message) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    $candidates = @($LogPath, (Join-Path $targetDir 'update.log')) | Select-Object -Unique
    foreach ($candidate in $candidates) {
        try {
            $dir = Split-Path -Parent $candidate
            if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
            Add-Content -LiteralPath $candidate -Value $line -Encoding UTF8
            return
        } catch {}
    }
}
function Write-Receipt([bool]$Confirmed, [string]$Message) {
    try {
        $dir = Split-Path -Parent $ReceiptPath
        if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $obj = [ordered]@{
            confirmed = $Confirmed
            version = $ExpectedVersion
            target = $TargetPath
            sha256 = $ExpectedSha256.ToLowerInvariant()
            message = $Message
            timestamp = (Get-Date).ToString('o')
        }
        $json = $obj | ConvertTo-Json -Depth 4
        $tmpReceipt = "$ReceiptPath.tmp"
        [System.IO.File]::WriteAllText($tmpReceipt, $json, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tmpReceipt -Destination $ReceiptPath -Force
    } catch {}
}
function Restore-Previous([string]$Reason) {
    Write-UpdateLog ("Начат откат: " + $Reason)
    if ($newProcess -and -not $newProcess.HasExited) {
        try { Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
        Start-Sleep -Milliseconds 700
    }
    if ($hadPrevious) {
        try {
            if ($backupReady -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
                if (Test-Path -LiteralPath $TargetPath) {
                    Remove-Item -LiteralPath $TargetPath -Force -ErrorAction SilentlyContinue
                }
                Copy-Item -LiteralPath $backup -Destination $TargetPath -Force
                if ($previousHash) {
                    $restoredHash = (Get-FileHash -LiteralPath $TargetPath -Algorithm SHA256).Hash.ToLowerInvariant()
                    if ($restoredHash -ne $previousHash) { throw 'Контрольная сумма резервной копии не совпала.' }
                }
            }
            if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
                $alreadyRunning = Get-CimInstance Win32_Process -Filter "Name='MoyaIshka.exe'" -ErrorAction SilentlyContinue |
                    Where-Object { $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq [System.IO.Path]::GetFullPath($TargetPath)) }
                if (-not $alreadyRunning) {
                    Start-Process -FilePath $TargetPath -WorkingDirectory $targetDir | Out-Null
                    Write-UpdateLog "Предыдущая версия восстановлена и запущена: $TargetPath"
                } else {
                    Write-UpdateLog "Предыдущий EXE остался запущен: $TargetPath"
                }
            } else {
                throw 'После отката исполняемый файл старой версии не найден.'
            }
        } catch {
            Write-UpdateLog ("КРИТИЧЕСКАЯ ОШИБКА отката: " + $_.Exception.Message)
        }
    } elseif (Test-Path -LiteralPath $TargetPath) {
        Remove-Item -LiteralPath $TargetPath -Force -ErrorAction SilentlyContinue
        Write-UpdateLog 'Предыдущей версии не было; неудачный новый EXE удалён.'
    }
}

try {
    Write-UpdateLog "Получена задача установки версии $ExpectedVersion. Целевой EXE: $TargetPath. Временный EXE: $StagedPath"
    if (-not (Test-Path -LiteralPath $StagedPath -PathType Leaf)) { throw "Не найден загруженный файл: $StagedPath" }

    # Wait for the app that requested the update to exit fully.
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $oldProcess = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
        if (-not $oldProcess) { break }
        Start-Sleep -Milliseconds 300
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        throw 'Старая ИИшка не закрылась за 120 секунд. Файл не заменён.'
    }

    # Close an older instance that may still be running from the destination EXE.
    # The user has explicitly accepted the update; request a normal window close first.
    $runningFromTarget = Get-CimInstance Win32_Process -Filter "Name='MoyaIshka.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq [System.IO.Path]::GetFullPath($TargetPath)) }
    foreach ($entry in @($runningFromTarget)) {
        if (-not $entry) { continue }
        $existing = Get-Process -Id ([int]$entry.ProcessId) -ErrorAction SilentlyContinue
        if ($existing) {
            Write-UpdateLog "Запрашиваю закрытие старого экземпляра (PID $($entry.ProcessId)) из $TargetPath."
            [void]$existing.CloseMainWindow()
            if (-not $existing.WaitForExit(20000)) {
                throw 'Старая копия в целевой папке не закрылась за 20 секунд. Закрой её вручную и повтори обновление.'
            }
        }
    }
    $lockDeadline = (Get-Date).AddSeconds(10)
    $runningFromTarget = $null
    while ((Get-Date) -lt $lockDeadline) {
        $runningFromTarget = Get-CimInstance Win32_Process -Filter "Name='MoyaIshka.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq [System.IO.Path]::GetFullPath($TargetPath)) }
        if (-not $runningFromTarget) { break }
        Start-Sleep -Milliseconds 300
    }
    if ($runningFromTarget) { throw 'Не удалось освободить EXE в целевой папке. Замена отменена, старая версия сохранена.' }

    $stagedHash = (Get-FileHash -LiteralPath $StagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($stagedHash -ne $ExpectedSha256.ToLowerInvariant()) { throw 'SHA-256 загруженного EXE не совпадает с релизом.' }

    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    $probe = Join-Path $targetDir ('.moya_update_write_test_' + [guid]::NewGuid().ToString('N'))
    [System.IO.File]::WriteAllText($probe, 'ok')
    Remove-Item -LiteralPath $probe -Force

    # Stage the replacement on the same volume as the final target.
    if (Test-Path -LiteralPath $tempTarget) { Remove-Item -LiteralPath $tempTarget -Force }
    Copy-Item -LiteralPath $StagedPath -Destination $tempTarget -Force
    $tempHash = (Get-FileHash -LiteralPath $tempTarget -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($tempHash -ne $ExpectedSha256.ToLowerInvariant()) { throw 'Копия EXE в папке установки повреждена. Замена отменена.' }

    # Save a byte-for-byte backup before touching the active executable.
    if ($hadPrevious) {
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        Copy-Item -LiteralPath $TargetPath -Destination $backup -Force
        $previousHash = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($previousHash -ne (Get-FileHash -LiteralPath $TargetPath -Algorithm SHA256).Hash.ToLowerInvariant()) {
            throw 'Не удалось создать проверенную резервную копию старой версии.'
        }
        $backupReady = $true
        if (Test-Path -LiteralPath $oldSwap) { Remove-Item -LiteralPath $oldSwap -Force }
        try {
            # Prefer the atomic same-volume replace operation.
            [System.IO.File]::Replace($tempTarget, $TargetPath, $oldSwap, $true)
        } catch {
            Write-UpdateLog ("Атомарная замена недоступна, использую безопасный резервный путь: " + $_.Exception.Message)
            if (Test-Path -LiteralPath $oldSwap) { Remove-Item -LiteralPath $oldSwap -Force }
            Move-Item -LiteralPath $TargetPath -Destination $oldSwap
            try {
                Move-Item -LiteralPath $tempTarget -Destination $TargetPath
            } catch {
                if (Test-Path -LiteralPath $oldSwap) { Move-Item -LiteralPath $oldSwap -Destination $TargetPath -Force }
                throw
            }
        }
    } else {
        Move-Item -LiteralPath $tempTarget -Destination $TargetPath
    }

    # Verify actual bytes and embedded Windows file-version metadata before launch.
    $installedHash = (Get-FileHash -LiteralPath $TargetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installedHash -ne $ExpectedSha256.ToLowerInvariant()) { throw 'После замены SHA-256 установленного EXE не совпадает.' }
    # Windows version resources are useful metadata, but some valid PyInstaller
    # builds expose empty ProductVersion/FileVersion in Explorer. Do not block
    # installation on missing metadata: the launched application must confirm
    # its own APP_VERSION, exact path and SHA-256 through the receipt handshake.
    $fileInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($TargetPath)
    $embeddedVersion = [string]$fileInfo.ProductVersion
    if ([string]::IsNullOrWhiteSpace($embeddedVersion)) { $embeddedVersion = [string]$fileInfo.FileVersion }
    if ([string]::IsNullOrWhiteSpace($embeddedVersion)) {
        Write-UpdateLog 'В свойствах Windows нет FileVersion/ProductVersion. Продолжаю: версию подтвердит само приложение при запуске.'
    } elseif ($embeddedVersion -notmatch [regex]::Escape($ExpectedVersion)) {
        throw "В EXE записана версия '$embeddedVersion', ожидалась '$ExpectedVersion'."
    } else {
        Write-UpdateLog "Windows version metadata совпадает: $embeddedVersion. Дополнительно проверю версию через запуск приложения."
    }

    if (Test-Path -LiteralPath $ReceiptPath) { Remove-Item -LiteralPath $ReceiptPath -Force }
    # Pass the confirmation data through the child process environment.
    # This avoids Windows command-line quoting issues for user folders containing
    # spaces, Cyrillic characters or other non-ASCII symbols.
    $env:MOYA_UPDATE_RECEIPT = $ReceiptPath
    $env:MOYA_UPDATE_VERSION = $ExpectedVersion
    $env:MOYA_UPDATE_SHA256 = $ExpectedSha256
    $env:MOYA_UPDATE_TARGET = $TargetPath

    $launchErrors = @()
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            $newProcess = Start-Process -FilePath $TargetPath -WorkingDirectory $targetDir -WindowStyle Normal -PassThru -ErrorAction Stop
            if ($newProcess) { break }
        } catch {
            $launchErrors += $_.Exception.Message
            Write-UpdateLog "Попытка запуска новой версии $attempt/3 завершилась ошибкой: $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }
    if (-not $newProcess) {
        throw ("Не удалось автоматически запустить установленный EXE после трёх попыток: " + ($launchErrors -join "; "))
    }
    $launchAttempt = 1
    Write-UpdateLog "Новая программа запущена (PID $($newProcess.Id)); ожидаю подтверждение версии и пути."

    # Do not claim success until the new application verifies itself after its GUI starts.
    $confirmDeadline = (Get-Date).AddSeconds(90)
    $confirmed = $false
    while ((Get-Date) -lt $confirmDeadline) {
        if (Test-Path -LiteralPath $ReceiptPath -PathType Leaf) {
            try {
                $receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($receipt.confirmed -eq $true -and
                    [string]$receipt.version -eq $ExpectedVersion -and
                    [string]$receipt.sha256 -eq $ExpectedSha256.ToLowerInvariant() -and
                    [string]$receipt.target -ieq $TargetPath) {
                    $confirmed = $true
                    break
                }
                if ($receipt.confirmed -eq $false) { throw "Новая версия отклонила запуск: $($receipt.message)" }
            } catch {
                if ($_.Exception.Message -like 'Новая версия отклонила запуск:*') { throw }
            }
        }
        if ($newProcess.HasExited) {
            $newProcess.Refresh()
            if ($launchAttempt -ge 3) {
                throw "Новая версия закрылась до подтверждения запуска после трёх попыток (код $($newProcess.ExitCode))."
            }
            $launchAttempt++
            Write-UpdateLog "Новая версия завершилась до подтверждения. Повторный запуск $launchAttempt/3."
            Start-Sleep -Seconds 2
            $newProcess = Start-Process -FilePath $TargetPath -WorkingDirectory $targetDir -WindowStyle Normal -PassThru -ErrorAction Stop
            continue
        }
        Start-Sleep -Milliseconds 400
    }
    if (-not $confirmed) { throw 'Новая версия не подтвердила запуск за 90 секунд. Выполняется откат.' }

    if (Test-Path -LiteralPath $oldSwap) { Remove-Item -LiteralPath $oldSwap -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $StagedPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ReceiptPath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "ПОДТВЕРЖДЕНО: версия $ExpectedVersion установлена и запущена из $TargetPath. SHA-256 проверен."
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-UpdateLog ("ОШИБКА УСТАНОВКИ: " + $message)
    Write-Receipt $false $message
    Restore-Previous $message
    if (Test-Path -LiteralPath $tempTarget) { Remove-Item -LiteralPath $tempTarget -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $oldSwap) { Remove-Item -LiteralPath $oldSwap -Force -ErrorAction SilentlyContinue }
    exit 1
} finally {
    try { if ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath)) { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue } } catch {}
}
'''


def write_powershell_updater_script(script_path: str | Path) -> Path:
    """Write the detached updater as a UTF-8-BOM Windows CRLF PowerShell file.

    Windows PowerShell 5.1 is sensitive to script encoding/newlines. Keeping this
    in a separately testable helper prevents an invalid ``newline`` argument from
    silently stopping the installation before PowerShell is launched.
    """
    path = Path(script_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\r\n") as stream:
        stream.write(_powershell_script())
    payload = path.read_bytes()
    if b"\r\n" not in payload or b"\n" in payload.replace(b"\r\n", b""):
        path.unlink(missing_ok=True)
        raise OSError("Не удалось создать PowerShell-скрипт с корректными переводами строк Windows (CRLF).")
    return path


def launch_windows_replacement(
    staged_path: str | Path,
    target_path: str | Path,
    parent_pid: int,
    log_path: str | Path,
    expected_version: str = "",
    receipt_path: str | Path | None = None,
    *,
    platform_name: str | None = None,
) -> Path:
    """Launch a detached updater and verify replacement using a startup handshake.

    ``platform_name`` is an internal test seam; production calls use ``os.name``.
    """
    if (platform_name or os.name) != "nt":
        raise OSError("Автоматическая замена EXE поддерживается только в Windows.")

    staged = Path(staged_path).resolve()
    target = Path(target_path).resolve()
    log = Path(log_path).resolve()
    receipt = Path(receipt_path).resolve() if receipt_path else log.with_name("update_confirmation.json")
    if not staged.is_file():
        raise FileNotFoundError(f"Файл обновления не найден: {staged}")
    if target.suffix.lower() != ".exe":
        raise ValueError("Путь установленного приложения должен оканчиваться на .exe")
    if not expected_version.strip():
        raise ValueError("Не передана ожидаемая версия обновления; установка без проверки версии запрещена.")

    digest = sha256_file(staged)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.unlink(missing_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    # Create the log before launching PowerShell. If the helper never starts,
    # the user still gets a clear record that launch was attempted.
    try:
        with log.open("a", encoding="utf-8") as stream:
            from datetime import datetime
            stream.write(f"{datetime.now().astimezone().isoformat()}  Запуск установщика обновления {expected_version.strip().lstrip('v')}.\n")
            stream.write(f"Целевой EXE: {target}\nВременный EXE: {staged}\n")
    except OSError as exc:
        raise OSError(f"Не удалось создать журнал обновления {log}: {exc}") from exc
    helper_fd, helper_name = tempfile.mkstemp(prefix="MoyaIshka_Updater_", suffix=".ps1")
    os.close(helper_fd)
    helper = Path(helper_name)
    try:
        write_powershell_updater_script(helper)

        # Prefer the Windows-provided PowerShell executable. A PATH entry can
        # point to a shim or an unexpected copy, which is especially undesirable
        # in a privileged file-replacement step.
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates: list[str] = []
        for candidate in (
            system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
            system_root / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
            system_root / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        ):
            if candidate.is_file():
                value = str(candidate)
                if os.path.normcase(value) not in {os.path.normcase(item) for item in candidates}:
                    candidates.append(value)
        for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
            found = shutil.which(name)
            if found and os.path.normcase(found) not in {os.path.normcase(item) for item in candidates}:
                candidates.append(found)
        if not candidates:
            raise FileNotFoundError("Windows PowerShell не найден. Он обычно входит в Windows 10.")

        command_tail = [
            "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(helper),
            str(staged), str(target), str(int(parent_pid)), digest,
            expected_version.strip().lstrip("v"), str(log), str(receipt),
        ]
        startupinfo = None
        startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_type is not None:
            startupinfo = startupinfo_type()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

        # CREATE_NO_WINDOW is sufficient to keep PowerShell invisible. Do not
        # combine it with DETACHED_PROCESS; retry without flags if Windows denies
        # process creation with the hidden-console flag.
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flag_attempts = [no_window]
        if no_window:
            flag_attempts.append(0)
        launch_errors: list[str] = []
        for powershell in candidates:
            command = [powershell, *command_tail]
            for flags in flag_attempts:
                options: dict[str, Any] = {"close_fds": True, "creationflags": flags}
                if startupinfo is not None:
                    options["startupinfo"] = startupinfo
                try:
                    process = subprocess.Popen(command, **options)
                except OSError as exc:
                    error_code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
                    launch_errors.append(
                        f"{powershell} (creationflags={flags}, error={error_code}): {exc}"
                    )
                    continue

                try:
                    with log.open("a", encoding="utf-8") as stream:
                        from datetime import datetime
                        stream.write(
                            f"{datetime.now().astimezone().isoformat()}  "
                            f"PowerShell-установщик запущен: {powershell}; PID: {getattr(process, 'pid', 'unknown')}\n"
                        )
                except OSError:
                    pass
                return helper

        details = "; ".join(launch_errors) or "Windows не вернул подробности ошибки."
        raise OSError(f"Не удалось запустить PowerShell-установщик. Попытки: {details}")
    except Exception as exc:
        helper.unlink(missing_ok=True)
        try:
            with log.open("a", encoding="utf-8") as stream:
                from datetime import datetime
                stream.write(f"{datetime.now().astimezone().isoformat()}  ОШИБКА запуска PowerShell-установщика: {exc}\n")
        except OSError:
            pass
        raise


# Backward-compatible entry point for older packaged builds.
def apply_update(staged_path: str, target_path: str, process_id: str) -> int:
    try:
        launch_windows_replacement(
            staged_path, target_path, int(process_id),
            user_install_dir() / "update.log", expected_version="4.1.7",
        )
        return 0
    except Exception:
        return 1


def _looks_like_moya_executable(filename: str) -> bool:
    """Recognize only this application's executable names."""
    name = filename.casefold()
    return (
        name in {"moyaishka.exe", "моя иишка.exe", "moyaishka_update.exe", "моя иишка_update.exe", "moyaishka.previous.exe", "moyaishka.installing.exe"}
        or (name.startswith("moyaishka_") and name.endswith(".exe"))
        or (name.startswith("моя иишка_") and name.endswith(".exe"))
    )


def _version_hint_from_path(path: Path) -> str:
    import re
    candidates = [path.stem, *reversed(path.parts[:-1])]
    for value in candidates:
        match = re.search(r"(?<!\d)v?(\d+\.\d+(?:\.\d+){0,2})(?!\d)", value, re.IGNORECASE)
        if match:
            return match.group(1)
    return "Не определена"


def scan_application_copies(
    configured_install_dir: str | Path | None = None,
    current_exe: str | Path | None = None,
    data_dir: str | Path | None = None,
    *,
    local_app_data: str | Path | None = None,
    roaming_app_data: str | Path | None = None,
    user_home: str | Path | None = None,
) -> list[dict[str, str | int | bool]]:
    """Find likely MoyaIshka EXE copies in common install/download locations.

    This intentionally does not scan the entire disk. It checks the active and
    configured install folders, per-user Programs folders, the app's updater
    staging folder, Desktop and Downloads. The currently running EXE is always
    excluded from results.
    """
    import datetime as _dt
    home = Path(user_home or Path.home()).expanduser()
    local = Path(local_app_data or os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(roaming_app_data or os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    running = Path(current_exe).expanduser().resolve() if current_exe else None

    roots: list[tuple[Path, int]] = []
    if configured_install_dir:
        roots.append((Path(configured_install_dir).expanduser(), 1))
    if current_exe:
        roots.append((Path(current_exe).expanduser().resolve().parent, 1))
    roots.extend([
        (local / "Programs", 4),
        (local / "MoyaIshka", 3),
        (roaming / "MoyaIshka" / "update_staging", 2),
        (home / "Desktop", 2),
        (home / "Downloads", 2),
    ])
    if data_dir:
        # Only inspect the updater staging folder, never the whole data directory.
        roots.append((Path(data_dir).expanduser() / "update_staging", 2))

    found: dict[str, dict[str, str | int | bool]] = {}
    prune_names = {
        "$recycle.bin", "windows", "system volume information", "node_modules",
        ".git", "__pycache__", "temp", "tmp",
    }
    for raw_root, max_depth in roots:
        try:
            root = raw_root.resolve()
            if not root.is_dir():
                continue
        except (OSError, RuntimeError):
            continue
        for directory, dirnames, filenames in os.walk(root):
            current_dir = Path(directory)
            try:
                depth = len(current_dir.relative_to(root).parts)
            except ValueError:
                depth = max_depth + 1
            dirnames[:] = [
                name for name in dirnames
                if name.casefold() not in prune_names and depth < max_depth
            ]
            for filename in filenames:
                if not _looks_like_moya_executable(filename):
                    continue
                candidate = current_dir / filename
                try:
                    resolved = candidate.resolve(strict=True)
                    if not resolved.is_file() or (running and resolved == running):
                        continue
                    stat = resolved.stat()
                    key = os.path.normcase(str(resolved))
                    parent_name = resolved.parent.name.casefold()
                    if parent_name == "update_staging":
                        category = "Временный файл обновления"
                    elif "programs" in {part.casefold() for part in resolved.parts}:
                        category = "Копия в папке программ"
                    else:
                        category = "Отдельная копия"
                    found[key] = {
                        "path": str(resolved),
                        "version": _version_hint_from_path(resolved),
                        "category": category,
                        "size": int(stat.st_size),
                        "modified": _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                        "is_current": False,
                    }
                except (OSError, RuntimeError):
                    continue

    return sorted(found.values(), key=lambda item: str(item["path"]).casefold())


def delete_application_copy(path: str | Path, current_exe: str | Path | None = None) -> None:
    """Delete a selected stale copy, refusing to delete the running executable."""
    target = Path(path).expanduser().resolve(strict=True)
    if current_exe:
        running = Path(current_exe).expanduser().resolve()
        if target == running:
            raise ValueError("Нельзя удалить приложение, которое сейчас запущено.")
    if not target.is_file() or not _looks_like_moya_executable(target.name):
        raise ValueError("Выбранный файл не распознан как исполняемый файл Моей ИИшки.")
    target.unlink()





def validate_app_directory(app_dir: str | Path, expected_version: str | None = None) -> dict[str, Any]:
    """Verify every staged file against the build manifest before any install action."""
    root = Path(app_dir).resolve()
    try:
        manifest = json.loads((root / "build_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Манифест сборки отсутствует или повреждён: {exc}") from exc
    version = str(manifest.get("version", "")).strip()
    if not version or (expected_version and version != expected_version.lstrip("v")):
        raise RuntimeError(f"Версия папки не совпадает с ожидаемой: {version or 'не указана'}.")
    if manifest.get("entrypoint") != "MoyaIshka.exe":
        raise RuntimeError("Неверная точка входа в манифесте.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 20000:
        raise RuntimeError("Манифест содержит неверный список файлов.")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("Повреждена запись файла в манифесте.")
        relative = PurePosixPath(str(item.get("path", "")).replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or any(
            part in {"", ".", ".."} or ":" in part for part in relative.parts
        ):
            raise RuntimeError("Манифест содержит небезопасный путь.")
        key = relative.as_posix().casefold()
        if key in seen:
            raise RuntimeError(f"Повторяющийся файл в манифесте: {relative}.")
        seen.add(key)
        path = root.joinpath(*relative.parts).resolve()
        if root not in path.parents or not path.is_file():
            raise RuntimeError(f"Файл отсутствует или выходит за пределы папки: {relative}.")
        if path.stat().st_size != int(item.get("size", -1)):
            raise RuntimeError(f"Размер файла не совпадает: {relative}.")
        expected = str(item.get("sha256", "")).lower()
        if len(expected) != 64 or sha256_file(path).lower() != expected:
            raise RuntimeError(f"SHA-256 не совпадает: {relative}.")
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Символическая ссылка запрещена в пакете: {path.relative_to(root)}.")
        if path.is_file() and path.relative_to(root).as_posix() != "build_manifest.json":
            actual_files.add(path.relative_to(root).as_posix().casefold())
    if actual_files != seen:
        missing = sorted(seen - actual_files)
        extra = sorted(actual_files - seen)
        details = []
        if missing:
            details.append("отсутствуют: " + ", ".join(missing[:8]))
        if extra:
            details.append("лишние: " + ", ".join(extra[:8]))
        raise RuntimeError("Состав папки не совпадает с манифестом (" + "; ".join(details) + ").")
    exe = root / "MoyaIshka.exe"
    if not exe.is_file() or sha256_file(exe).lower() != str(manifest.get("exe_sha256", "")).lower():
        raise RuntimeError("Главный EXE не прошёл проверку SHA-256.")
    for name in ("python311.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        if not (root / "_internal" / name).is_file():
            raise RuntimeError(f"В папке отсутствует _internal/{name}.")
    return manifest


def _directory_updater_script() -> str:
    """PowerShell 5.1 transaction for folder replacement with backup and rollback."""
    return r'''param(
        [Parameter(Mandatory=$true)][string]$StagedDir,
        [Parameter(Mandatory=$true)][string]$TargetDir,
        [Parameter(Mandatory=$true)][int]$ParentPid,
        [Parameter(Mandatory=$true)][string]$ExpectedVersion,
        [Parameter(Mandatory=$true)][string]$LogPath,
        [Parameter(Mandatory=$true)][string]$ReceiptPath
    )
    $ErrorActionPreference = 'Stop'
    $StagedDir = [IO.Path]::GetFullPath($StagedDir)
    $TargetDir = [IO.Path]::GetFullPath($TargetDir)
    $Parent = Split-Path -Parent $TargetDir
    $Name = Split-Path -Leaf $TargetDir
    $TempDir = Join-Path $Parent ($Name + '.installing')
    $BackupDir = Join-Path $Parent ($Name + '.previous')
    $NewInstalled = $false
    $BackupReady = $false
    $NewProcess = $null
    $ExeHash = ''

    function Log([string]$Message) {
        try {
            Add-Content -LiteralPath $LogPath -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) -Encoding UTF8
        } catch {}
    }

    function VerifyFolder([string]$Folder) {
        $manifestPath = Join-Path $Folder 'build_manifest.json'
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Не найден build_manifest.json.' }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$manifest.version -ne $ExpectedVersion) { throw "Ожидалась версия $ExpectedVersion, получена $($manifest.version)." }
        if ([string]$manifest.entrypoint -ne 'MoyaIshka.exe') { throw 'Неверная точка входа.' }
        $items = @($manifest.files)
        if ($items.Count -lt 1 -or $items.Count -gt 20000) { throw 'Неверное число файлов в манифесте.' }
        $manifestFullPath = [IO.Path]::GetFullPath($manifestPath)
        $expectedPaths = @($items | ForEach-Object { ([string]$_.path).Replace('/', '\').ToLowerInvariant() } | Sort-Object -Unique)
        $treeItems = @(Get-ChildItem -LiteralPath $Folder -Force -Recurse)
        $reparsePoints = @($treeItems | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 })
        if ($reparsePoints.Count -gt 0) { throw 'Символические ссылки и reparse points в папке приложения запрещены.' }
        $base = [IO.Path]::GetFullPath($Folder).TrimEnd('\') + '\'
        $actualPaths = @(
            Get-ChildItem -LiteralPath $Folder -File -Force -Recurse |
                Where-Object { [IO.Path]::GetFullPath($_.FullName) -ine $manifestFullPath } |
                ForEach-Object { $_.FullName.Substring($base.Length).Replace('\', '/').ToLowerInvariant() } |
                Sort-Object -Unique
        )
        if ($actualPaths.Count -ne $expectedPaths.Count -or
            @(Compare-Object -ReferenceObject $expectedPaths -DifferenceObject $actualPaths).Count -gt 0) {
            throw 'Фактический состав папки не совпадает с build_manifest.json.'
        }
        foreach ($item in $items) {
            $relative = ([string]$item.path).Replace('/', '\')
            if ([string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative) -or $relative -match '(^|\\)\.\.(\\|$)|:') {
                throw "Небезопасный путь: $relative"
            }
            $path = [IO.Path]::GetFullPath((Join-Path $Folder $relative))
            if (-not $path.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { throw "Путь вне пакета: $relative" }
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Отсутствует файл: $relative" }
            if ([long](Get-Item -LiteralPath $path).Length -ne [long]$item.size) { throw "Размер не совпадает: $relative" }
            $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne ([string]$item.sha256).ToLowerInvariant()) { throw "SHA-256 не совпадает: $relative" }
        }
        $exe = Join-Path $Folder 'MoyaIshka.exe'
        $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne ([string]$manifest.exe_sha256).ToLowerInvariant()) { throw 'SHA-256 EXE не совпадает.' }
        foreach ($dll in @('python311.dll','vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll')) {
            if (-not (Test-Path -LiteralPath (Join-Path $Folder ('_internal\' + $dll)) -PathType Leaf)) { throw "Не найден _internal\$dll" }
        }
        return $hash
    }

    function Rollback([string]$Reason) {
        Log "Откат: $Reason"
        if ($NewProcess -and -not $NewProcess.HasExited) {
            try {
                Stop-Process -Id $NewProcess.Id -Force -ErrorAction SilentlyContinue
                [void]$NewProcess.WaitForExit(15000)
            } catch {}
            try { $NewProcess.Refresh() } catch {}
        }
        try {
            if ($NewInstalled -and (Test-Path -LiteralPath $TargetDir)) {
                Remove-Item -LiteralPath $TargetDir -Recurse -Force
            }
            if ($BackupReady -and (Test-Path -LiteralPath $BackupDir -PathType Container)) {
                if (Test-Path -LiteralPath $TargetDir) { Remove-Item -LiteralPath $TargetDir -Recurse -Force }
                Move-Item -LiteralPath $BackupDir -Destination $TargetDir
                Log 'Резервная папка восстановлена.'
                Remove-Item Env:MOYA_UPDATE_RECEIPT -ErrorAction SilentlyContinue
                Remove-Item Env:MOYA_UPDATE_VERSION -ErrorAction SilentlyContinue
                Remove-Item Env:MOYA_UPDATE_SHA256 -ErrorAction SilentlyContinue
                Remove-Item Env:MOYA_UPDATE_TARGET -ErrorAction SilentlyContinue
                Start-Process -FilePath (Join-Path $TargetDir 'MoyaIshka.exe') -WorkingDirectory $TargetDir | Out-Null
            } elseif (-not $BackupReady -and $NewInstalled) {
                Log 'Старой установки не было; неудачная новая папка удалена.'
            }
        } catch { Log "КРИТИЧЕСКАЯ ОШИБКА ОТКАТА: $($_.Exception.Message)" }
    }

    try {
        Log "Начата установка onedir ${ExpectedVersion}: $StagedDir -> $TargetDir"
        if (-not (Test-Path -LiteralPath $StagedDir -PathType Container)) { throw "Не найдена папка обновления: $StagedDir" }
        $runningDeadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $runningDeadline -and (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) {
            Start-Sleep -Milliseconds 300
        }
        if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) { throw 'Программа не закрылась за 120 секунд.' }

        $running = Get-CimInstance Win32_Process -Filter "Name='MoyaIshka.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq (Join-Path $TargetDir 'MoyaIshka.exe')) }
        foreach ($entry in @($running)) {
            if (-not $entry) { continue }
            $proc = Get-Process -Id ([int]$entry.ProcessId) -ErrorAction SilentlyContinue
            if ($proc) {
                [void]$proc.CloseMainWindow()
                if (-not $proc.WaitForExit(20000)) { throw 'Другой экземпляр не закрылся; обновление отменено.' }
            }
        }

        $stagedHash = VerifyFolder $StagedDir
        if (Test-Path -LiteralPath $TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
        $copy = Join-Path $env:SystemRoot 'System32\robocopy.exe'
        & $copy $StagedDir $TempDir /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -gt 7) { throw "Не удалось скопировать папку (robocopy $LASTEXITCODE)." }
        $copiedHash = VerifyFolder $TempDir
        if ($copiedHash -ne $stagedHash) { throw 'Контрольная сумма главного EXE после копирования не совпала.' }

        $hadPrevious = $false
        if (Test-Path -LiteralPath $TargetDir -PathType Container) {
            $oldItems = @(Get-ChildItem -LiteralPath $TargetDir -Force -ErrorAction Stop)
            $hadPrevious = ($oldItems.Count -gt 0)
            if ($hadPrevious -and -not (
                (Test-Path -LiteralPath (Join-Path $TargetDir 'MoyaIshka.exe') -PathType Leaf) -or
                (Test-Path -LiteralPath (Join-Path $TargetDir 'build_manifest.json') -PathType Leaf)
            )) {
                throw 'Целевая папка уже содержит другие файлы и не распознана как установка Моей ИИшки. Выбери отдельную папку.'
            }
        }

        if ($hadPrevious) {
            if (Test-Path -LiteralPath $BackupDir) {
                $saved = $BackupDir + '.' + (Get-Date -Format 'yyyyMMddHHmmss')
                Move-Item -LiteralPath $BackupDir -Destination $saved
                Log "Старая резервная копия сохранена: $saved"
            }
            Move-Item -LiteralPath $TargetDir -Destination $BackupDir
            $BackupReady = $true
        } elseif (Test-Path -LiteralPath $TargetDir) {
            Remove-Item -LiteralPath $TargetDir -Recurse -Force
        }

        Move-Item -LiteralPath $TempDir -Destination $TargetDir
        $NewInstalled = $true
        $ExeHash = VerifyFolder $TargetDir

        if (Test-Path -LiteralPath $ReceiptPath) { Remove-Item -LiteralPath $ReceiptPath -Force }
        $env:MOYA_UPDATE_RECEIPT = $ReceiptPath
        $env:MOYA_UPDATE_VERSION = $ExpectedVersion
        $env:MOYA_UPDATE_SHA256 = $ExeHash
        $env:MOYA_UPDATE_TARGET = Join-Path $TargetDir 'MoyaIshka.exe'
        $NewProcess = Start-Process -FilePath $env:MOYA_UPDATE_TARGET -WorkingDirectory $TargetDir -PassThru -ErrorAction Stop
        Log "Новая программа запущена PID $($NewProcess.Id); проверяю подтверждение."
        $limit = (Get-Date).AddSeconds(90)
        $confirmed = $false
        while ((Get-Date) -lt $limit) {
            if (Test-Path -LiteralPath $ReceiptPath -PathType Leaf) {
                try {
                    $receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($receipt.confirmed -eq $true -and [string]$receipt.version -eq $ExpectedVersion -and
                        [string]$receipt.sha256 -eq $ExeHash -and [string]$receipt.target -ieq (Join-Path $TargetDir 'MoyaIshka.exe')) {
                        $confirmed = $true
                        break
                    }
                    if ($receipt.confirmed -eq $false) { throw "Приложение отклонило запуск: $($receipt.message)" }
                } catch {
                    if ($_.Exception.Message -like 'Приложение отклонило запуск:*') { throw }
                }
            }
            if ($NewProcess.HasExited) { throw "Новая версия завершилась до подтверждения (код $($NewProcess.ExitCode))." }
            Start-Sleep -Milliseconds 400
        }
        if (-not $confirmed) { throw 'Новая версия не подтвердила запуск за 90 секунд.' }
        Remove-Item -LiteralPath $ReceiptPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $StagedDir -Recurse -Force -ErrorAction SilentlyContinue
        Log "УСПЕХ: версия $ExpectedVersion работает. Резервная копия: $BackupDir"
        try { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue } catch {}
        exit 0
    } catch {
        $message = $_.Exception.Message
        Log "ОШИБКА установки: $message"
        try {
            $failed = [ordered]@{
                confirmed = $false; version = $ExpectedVersion; target = $TargetDir
                sha256 = $ExeHash; message = $message; timestamp = (Get-Date).ToString('o')
            }
            $tempReceipt = "$ReceiptPath.tmp"
            [IO.File]::WriteAllText($tempReceipt, ($failed | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
            Move-Item -LiteralPath $tempReceipt -Destination $ReceiptPath -Force
        } catch {}
        Rollback $message
        if (Test-Path -LiteralPath $TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue }
        try { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue } catch {}
        exit 1
    }
    '''


def launch_windows_directory_replacement(
    staged_app_dir: str | Path,
    target_dir: str | Path,
    parent_pid: int,
    log_path: str | Path,
    expected_version: str,
    receipt_path: str | Path | None = None,
    *,
    platform_name: str | None = None,
) -> Path:
    """Launch a detached Windows helper to replace the entire onedir folder."""
    if (platform_name or os.name) != "nt":
        raise OSError("Замена папки приложения поддерживается только в Windows.")
    staged = Path(staged_app_dir).resolve()
    target = ensure_install_dir_writable(target_dir)
    if staged == target or target in staged.parents or staged in target.parents:
        raise ValueError("Папка обновления и папка установки должны быть разными.")
    if not expected_version.strip():
        raise ValueError("Не указана ожидаемая версия.")
    validate_app_directory(staged, expected_version)

    log = Path(log_path).resolve()
    receipt = Path(receipt_path).resolve() if receipt_path else log.with_name("update_confirmation.json")
    log.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.unlink(missing_ok=True)
    helper_fd, helper_name = tempfile.mkstemp(prefix="MoyaIshka_DirUpdater_", suffix=".ps1")
    os.close(helper_fd)
    helper = Path(helper_name)
    try:
        with helper.open("w", encoding="utf-8-sig", newline="\r\n") as stream:
            stream.write(_directory_updater_script())
        payload = helper.read_bytes()
        if b"\r\n" not in payload or b"\n" in payload.replace(b"\r\n", b""):
            raise OSError("Не удалось создать PowerShell-скрипт с CRLF.")

        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates = [
            system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
            system_root / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        ]
        powershell = next((str(path) for path in candidates if path.is_file()), shutil.which("powershell.exe"))
        if not powershell:
            raise FileNotFoundError("Windows PowerShell не найден.")
        command = [
            powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(helper),
            str(staged), str(target), str(int(parent_pid)), expected_version.lstrip("v"),
            str(log), str(receipt),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(command, close_fds=True, creationflags=creation_flags)
        except OSError:
            if not creation_flags:
                raise
            subprocess.Popen(command, close_fds=True, creationflags=0)
        with log.open("a", encoding="utf-8") as stream:
            from datetime import datetime
            stream.write(
                f"{datetime.now().astimezone().isoformat()}  "
                f"Запущена безопасная замена всей папки версии {expected_version}.\n"
            )
            stream.write(f"Источник: {staged}\nПапка установки: {target}\n")
        return helper
    except Exception as exc:
        helper.unlink(missing_ok=True)
        try:
            with log.open("a", encoding="utf-8") as stream:
                from datetime import datetime
                stream.write(f"{datetime.now().astimezone().isoformat()}  Ошибка запуска установщика: {exc}\n")
        except OSError:
            pass
        raise
