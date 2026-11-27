# -*- coding: utf-8 -*-
"""Provision and launch Windows Sandbox for untrusted self-improvement tests.

Only a disposable source copy and a dedicated result directory are mapped into
Windows Sandbox. The user's profile, installation, cloud folders and network are
never shared with the guest.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

FEATURE_NAME = "Containers-DisposableClientVM"
MINIMUM_BUILD = 18362
SUPPORTED_EDITIONS = {"Professional", "ProfessionalN", "Enterprise", "Education", "EnterpriseS", "IoTEnterprise"}
DEFAULT_TIMEOUT_SECONDS = 600
MAX_MEMORY_MB = 4096


def _powershell_path() -> Path:
    root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _host_details() -> dict[str, Any]:
    if os.name != "nt":
        return {"supported": False, "reason": "Windows Sandbox доступна только в Windows 10/11."}
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        ) as key:
            edition = str(winreg.QueryValueEx(key, "EditionID")[0])
            build = int(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
    except (OSError, ValueError, TypeError):
        version = __import__("sys").getwindowsversion()
        edition, build = "", int(version.build)
    if build < MINIMUM_BUILD:
        return {
            "supported": False, "edition": edition, "build": build,
            "reason": "Нужна Windows 10 версии 1903 или новее.",
        }
    if edition and edition not in SUPPORTED_EDITIONS:
        return {
            "supported": False, "edition": edition, "build": build,
            "reason": "Windows Sandbox не поддерживается этой редакцией Windows.",
        }
    return {"supported": True, "edition": edition, "build": build, "reason": ""}


def get_windows_sandbox_status() -> dict[str, Any]:
    """Return a fail-closed status; an unknown feature state is never considered ready."""
    details = _host_details()
    result: dict[str, Any] = {
        **details, "installed": False, "ready": False, "restart_required": False,
        "feature_state": "Unknown", "virtualization_ready": False,
    }
    if not details.get("supported"):
        return result
    powershell = _powershell_path()
    if not powershell.is_file():
        result["reason"] = "Не найден системный Windows PowerShell."
        return result
    command = (
        "$ErrorActionPreference='Stop'; "
        "$f=Get-WindowsOptionalFeature -Online -FeatureName 'Containers-DisposableClientVM'; "
        "$c=Get-CimInstance Win32_ComputerSystem; "
        "$p=Get-CimInstance Win32_Processor; "
        "Write-Output ('MOYA_STATE=' + [string]$f.State); "
        "Write-Output ('MOYA_HYPERVISOR=' + [string]$c.HypervisorPresent); "
        "Write-Output ('MOYA_VIRT=' + [string](($p | Where-Object {$_.VirtualizationFirmwareEnabled -ne $true}).Count -eq 0)); "
        "Write-Output ('MOYA_MEM_MB=' + [string][math]::Floor($c.TotalPhysicalMemory / 1MB)); "
        "Write-Output ('MOYA_CORES=' + [string](($p | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum))"
    )
    try:
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
            creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"Не удалось проверить компонент Windows Sandbox: {exc}"
        return result
    if completed.returncode != 0:
        result["reason"] = (
            "Windows не разрешила проверить состояние Windows Sandbox. "
            "Проверь компонент через кнопку установки и системный запрос UAC. "
            + (completed.stderr or completed.stdout).strip()[-500:]
        )
        return result
    values: dict[str, str] = {}
    for line in (completed.stdout or "").splitlines():
        if line.startswith("MOYA_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip()
    state = values.get("MOYA_STATE", "Unknown")
    result["feature_state"] = state
    result["restart_required"] = state in {"EnablePending", "DisablePending"}
    result["installed"] = state == "Enabled"
    result["virtualization_ready"] = values.get("MOYA_VIRT", "").casefold() == "true"
    result["memory_mb"] = int(values.get("MOYA_MEM_MB", "0") or 0)
    result["logical_processors"] = int(values.get("MOYA_CORES", "0") or 0)
    executable = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
    result["ready"] = (
        result["installed"] and not result["restart_required"]
        and result["virtualization_ready"] and result["memory_mb"] >= 4096
        and result["logical_processors"] >= 2 and executable.is_file()
    )
    if result["restart_required"]:
        result["reason"] = "Windows Sandbox включена не полностью. Перезагрузи компьютер."
    elif not result["installed"]:
        result["reason"] = "Компонент Windows Sandbox ещё не установлен."
    elif not result["virtualization_ready"]:
        result["reason"] = "Включи Intel VT-x / AMD-V (виртуализацию) в BIOS/UEFI и перезагрузи ПК."
    elif result["memory_mb"] < 4096 or result["logical_processors"] < 2:
        result["reason"] = "Для Windows Sandbox нужно не менее 4 ГБ ОЗУ и двух логических процессоров."
    elif not executable.is_file():
        result["reason"] = "Компонент отмечен как включённый, но WindowsSandbox.exe не найден."
    elif values.get("MOYA_HYPERVISOR", "").casefold() != "true":
        result["reason"] = "Гипервизор Windows не запущен. Проверь виртуализацию и перезагрузи ПК."
        result["ready"] = False
    else:
        result["reason"] = "Windows Sandbox готова к изолированному тестированию."
    return result


def request_windows_sandbox_install() -> Path:
    """Start the built-in optional-feature installation through a visible UAC prompt."""
    details = _host_details()
    if not details.get("supported"):
        raise RuntimeError(str(details.get("reason") or "Этот компьютер не поддерживает Windows Sandbox."))
    powershell = _powershell_path()
    if not powershell.is_file():
        raise RuntimeError("Не найден Windows PowerShell.")
    work = Path(tempfile.mkdtemp(prefix="moya_sandbox_setup_"))
    result_path = work / "install_result.json"
    script_path = work / "install_windows_sandbox.ps1"
    result_literal = str(result_path).replace("'", "''")
    script = f"""$ErrorActionPreference = 'Stop'
$resultPath = '{result_literal}'
try {{
    $feature = Get-WindowsOptionalFeature -Online -FeatureName '{FEATURE_NAME}'
    $restartNeeded = $false
    if ([string]$feature.State -ne 'Enabled') {{
        $enableResult = Enable-WindowsOptionalFeature -Online -FeatureName '{FEATURE_NAME}' -All -NoRestart
        $restartNeeded = [bool]$enableResult.RestartNeeded
    }}
    $current = Get-WindowsOptionalFeature -Online -FeatureName '{FEATURE_NAME}'
    if ([string]$current.State -eq 'EnablePending') {{ $restartNeeded = $true }}
    $data = [ordered]@{{success=([string]$current.State -in @('Enabled','EnablePending')); state=[string]$current.State; restart_required=$restartNeeded; error=''}}
}} catch {{
    $data = [ordered]@{{success=$false; state='Error'; restart_required=$false; error=$_.Exception.Message}}
}}
$data | ConvertTo-Json -Compress | Set-Content -LiteralPath $resultPath -Encoding UTF8
"""
    script_path.write_text(script, encoding="utf-8")
    try:
        shell = ctypes.windll.shell32.ShellExecuteW
        shell.restype = ctypes.c_void_p
        code = shell(None, "runas", str(powershell), f'-NoProfile -ExecutionPolicy Bypass -File "{script_path}"', str(work), 1)
    except Exception as exc:
        raise RuntimeError(f"Не удалось запросить права администратора: {exc}") from exc
    if int(code or 0) <= 32:
        raise RuntimeError("Установка отменена или Windows не смогла открыть запрос прав администратора.")
    return result_path


def wait_for_install_result(result_path: str | Path, timeout_seconds: int = 900) -> dict[str, Any]:
    path = Path(result_path)
    deadline = time.monotonic() + max(30, min(int(timeout_seconds), 1800))
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                import json
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                pass
        time.sleep(1)
    raise TimeoutError("Не получен результат установки Windows Sandbox. Проверь окно PowerShell и повтори проверку.")


def create_sandbox_config(
    readonly_host_dir: str | Path,
    writable_host_dir: str | Path,
    config_path: str | Path,
    *,
    memory_mb: int = MAX_MEMORY_MB,
    command: str = r"powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\MoyaTest\ReadOnly\run_tests.ps1",
) -> Path:
    """Create a hardened WSB file: no network/clipboard/devices, read-only project, isolated output."""
    readonly = Path(readonly_host_dir).resolve()
    writable = Path(writable_host_dir).resolve()
    target = Path(config_path).resolve()
    if not readonly.is_dir() or not writable.is_dir():
        raise ValueError("Папки тестового пакета и результатов должны существовать.")
    if readonly == writable or readonly in writable.parents or writable in readonly.parents:
        raise ValueError("Папка результатов должна быть отдельной от тестового пакета.")
    memory = max(2048, min(int(memory_mb), 8192))
    xml = f"""<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>{memory}</MemoryInMB>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>{escape(str(readonly))}</HostFolder>
      <SandboxFolder>C:\\MoyaTest\\ReadOnly</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>{escape(str(writable))}</HostFolder>
      <SandboxFolder>C:\\MoyaTest\\Result</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>{escape(command)}</Command>
  </LogonCommand>
</Configuration>
"""
    target.write_text(xml, encoding="utf-8")
    return target


def launch_windows_sandbox(config_path: str | Path) -> subprocess.Popen:
    """Launch a WSB configuration only after the OS feature is fully ready."""
    status = get_windows_sandbox_status()
    if not status.get("ready"):
        raise RuntimeError(str(status.get("reason") or "Windows Sandbox не готова."))
    executable = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
    config = Path(config_path).resolve()
    if config.suffix.casefold() != ".wsb" or not config.is_file():
        raise ValueError("Не найден корректный файл конфигурации Windows Sandbox.")
    return subprocess.Popen(
        [str(executable), str(config)],
        cwd=str(config.parent),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def wait_for_sandbox_completion(
    result_dir: str | Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    sandbox_process: subprocess.Popen | None = None,
    max_result_bytes: int = 16 * 1024 * 1024,
) -> dict[str, Any]:
    """Wait for guest completion while bounding untrusted files written to the host share."""
    import json
    directory = Path(result_dir).resolve()
    completion = directory / "complete.json"
    deadline = time.monotonic() + max(60, min(int(timeout_seconds) + 120, 900))
    while time.monotonic() < deadline:
        total_bytes = 0
        try:
            for item in directory.rglob("*"):
                if item.is_symlink():
                    raise RuntimeError("Sandbox создала ссылку в папке результатов; отчёт отклонён.")
                if item.is_file():
                    total_bytes += item.stat().st_size
                    if total_bytes > max_result_bytes:
                        if sandbox_process is not None and sandbox_process.poll() is None:
                            sandbox_process.kill()
                        raise RuntimeError("Sandbox превысила лимит 16 МБ для отчётов; процесс остановлен.")
        except OSError as exc:
            raise RuntimeError(f"Не удалось проверить результаты Windows Sandbox: {exc}") from exc
        process_exited = sandbox_process is None or sandbox_process.poll() is not None
        if process_exited and completion.is_file() and not completion.is_symlink():
            try:
                if completion.stat().st_size <= 16 * 1024:
                    data = json.loads(completion.read_text(encoding="utf-8-sig"))
                    if isinstance(data, dict) and data.get("schema") == 1 and data.get("completed") is True:
                        return data
            except (OSError, ValueError):
                pass
        time.sleep(1)
    if sandbox_process is not None and sandbox_process.poll() is None:
        sandbox_process.kill()
    raise TimeoutError("Windows Sandbox не вернула результат за отведённое время. Изменения не применены.")
