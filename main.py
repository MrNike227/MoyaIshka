#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISH — настраиваемый настольный ИИ-помощник для Windows 10/11.

Базовая версия использует только стандартную библиотеку Python и Tkinter.
Для чтения PDF и DOCX поддерживаются необязательные пакеты pypdf и python-docx.
"""
from __future__ import annotations

# Standard library
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

# Lightweight test entry point: avoid importing the desktop application in Sandbox mode.
if __name__ == "__main__" and len(sys.argv) >= 4 and sys.argv[1] == "--run-proposal-tests":
    from improvement_testing import run_proposal_tests_cli
    raise SystemExit(run_proposal_tests_cli(sys.argv[2], sys.argv[3]))

# Required encryption dependency; keep imports explicit for PyInstaller analysis.
from cryptography import __version__ as CRYPTOGRAPHY_VERSION
from cryptography.exceptions import InvalidTag as _CryptoInvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _CryptoAESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt as _CryptoScrypt

# Core desktop interface, memory and plugin services.
from avatar_widget import AvatarWindow
from auto_memory import extract_memory_candidate
from context_engine import trim_conversation_context
from conversation_store import Store, load_json, now_iso, pretty_time
from intelligent_memory import (
    MEMORY_CONTEXT_END,
    MEMORY_CONTEXT_START,
    add_or_update_memory,
    edit_memory_entry as edit_memory_record,
    merge_memory_data,
    normalize_memory_metadata,
    select_memory_records,
)
from plugin_manager import PluginManager
from resource_governor import ResourceGovernor
from secure_storage import DATA_BLOB, dpapi_transform, protect_api_key, unprotect_api_key, unprotect_github_pr_token

# Backup and cloud storage integrations.
from backup_cloud import (
    BackupEncryptionError,
    decrypt_backup_file,
    encrypt_backup_file,
    sha256_file as backup_sha256_file,
)
from backup_manager import (
    copy_verified_backups,
    create_backup,
    inspect_backup,
    maybe_create_daily_backup,
    restore_backup,
)
from cloud_sync import (
    CloudSyncError,
    SupabaseCloudClient,
    decrypt_vault,
    delete_protected_session,
    encrypt_vault,
    load_protected_session,
    save_protected_session,
    snapshot_digest,
)
from google_drive_sync import (
    GoogleDriveError,
    GoogleDriveVaultClient,
    backup_file_md5,
)
from supabase_backup import (
    MAX_SUPABASE_FILE_BYTES,
    SupabaseBackupClient,
    SupabaseBackupError,
)

# Self-development, dependency analysis and safe proposal delivery.
from background_developer import run_background_development
from github_pr import (
    GitHubProposalError,
    create_draft_pull_request,
    create_draft_pull_request_from_bundle,
    find_matching_tested_proposal_bundle,
)
from improvement_delivery import (
    build_improvement_chat_context,
    default_install_root,
    ensure_install_layout,
    load_chat_improvement_ideas,
    resolve_install_layout,
    resolve_updates_directory,
    save_change_bundle,
    save_chat_improvement_idea,
    save_improvement_suggestions,
    save_multi_change_bundle,
)
from improvement_testing import test_change_bundle
from module_dependency_analyzer import (
    analyze_module_dependencies,
    build_architecture_intelligence_context,
    build_dependency_context,
    build_project_architecture_index,
    format_dependency_report,
    format_project_architecture_index,
)
from architecture_knowledge import (
    format_recent_architecture_lessons,
    refresh_architecture_knowledge,
)
from self_improvement import (
    MAX_SELF_IMPROVEMENT_CONTEXT_CHARS,
    apply_multi_file_unified_diff,
    apply_patch_with_backup,
    apply_unified_diff,
    build_model_source_context,
    build_multi_file_repair_context,
    editable_project_files,
    editable_test_files,
    extract_unified_diff,
    generate_validated_multi_file_unified_diff,
    generate_validated_unified_diff,
    parse_improvement_suggestions,
    project_dir,
    related_test_path,
    split_multi_file_unified_diff,
)

# Updates, voice input/output, desktop control and online research.
from temporary_storage import configure_temporary_storage, get_temp_directory, resolve_container_root
from updater import check_latest_release, download_verified_update
from updater_helper import (
    cleanup_old_application_copies,
    delete_application_copy,
    ensure_install_dir_writable,
    install_from_source,
    launch_windows_directory_replacement,
    launch_windows_replacement,
    resolve_install_dir,
    scan_application_copies,
    sha256_file,
    user_install_dir,
    validate_app_directory,
)
from voice_assistant import listen_once, speak_text, unload_piper_voice
from voice_manager import (
    VOICE_CATALOG,
    delete_voice,
    download_voice,
    installed_voice_ids,
    is_voice_installed,
    voice_label,
    voice_paths,
)
from web_research import (
    ResearchError,
    ResearchLibrary,
    format_research_digest,
    search_and_fetch,
)
from windows_control import handle_voice_command, launch_app, open_known_folder
from windows_sandbox import (
    get_windows_sandbox_status,
    record_windows_sandbox_install,
    request_windows_sandbox_install,
    wait_for_install_result,
)

# ISHApp feature modules — implementation is split by application responsibility.
import app_initialization_interface as _app_initialization_interface
import app_lifecycle_interface as _app_lifecycle_interface
import app_runtime as _app_runtime
import backup_interface as _backup_interface
import chat_interface as _chat_interface
import cloud_interface as _cloud_interface
import memory_interface as _memory_interface
import model_settings_interface as _model_settings_interface
import plugin_interface as _plugin_interface
import research_interface as _research_interface
import self_improvement_interface as _self_improvement_interface
import settings_interface as _settings_interface
import ui_components as _ui_components
import ui_pages_interface as _ui_pages_interface
import ui_theme_interface as _ui_theme_interface
import update_interface as _update_interface
import voice_interface as _voice_interface
PurpleCheckbox = _ui_components.PurpleCheckbox

APP_NAME = "ISH"
APP_VERSION = "4.3.1"
MAX_CONTEXT_MESSAGES = 32
MAX_ATTACHMENT_CHARS = 40_000

LEGACY_UPDATE_REPOSITORIES = frozenset({
    "MrNike227/MoyaIshka_v4.1.1",
})


def migrate_update_repository(config: dict[str, Any]) -> bool:
    """Migrate only known obsolete defaults; preserve user-selected repositories."""
    current = str(config.get("update_repo", "")).strip()
    if not current or current in LEGACY_UPDATE_REPOSITORIES:
        target = str(DEFAULT_CONFIG["update_repo"])
        changed = current != target
        config["update_repo"] = target
        return changed
    return False


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    directory = Path(base) / "ISH" if base else Path.home() / ".ish"
    legacy_directory = Path(base) / "MoyaIshka" if base else Path.home() / ".moya_ishka"
    if legacy_directory.is_dir() and not legacy_directory.is_symlink():
        directory.mkdir(parents=True, exist_ok=True)
        for legacy_item in legacy_directory.rglob("*"):
            if legacy_item.is_symlink():
                continue
            destination = directory / legacy_item.relative_to(legacy_directory)
            if legacy_item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(legacy_item, destination)
                except OSError:
                    pass
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_backup_directory() -> Path:
    """Place backups beside, never inside, the replaceable application folder."""
    app_folder = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return app_folder.parent / "ISH_Backups"


DATA_DIR = user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "assistant.db"
CLOUD_SESSION_PATH = DATA_DIR / "cloud_session.dat"
GOOGLE_DRIVE_SESSION_PATH = DATA_DIR / "google_drive_session.dat"
BACKUP_UPLOAD_QUEUE_PATH = DATA_DIR / "backup_upload_queue.json"
BACKUP_SUPABASE_UPLOAD_QUEUE_PATH = DATA_DIR / "backup_supabase_upload_queue.json"
if os.environ.get("APPDATA"):
    LEGACY_DATA_DIR = Path(os.environ["APPDATA"]) / "CustomAI"
else:
    LEGACY_DATA_DIR = Path.home() / ".custom_ai"

DEFAULT_CONFIG: dict[str, Any] = {
    "assistant_name": "ISH",
    "role": "Универсальный персональный ИИ-помощник",
    "personality": "Дружелюбный, внимательный, любознательный и спокойный. Действуй инициативно, но не навязывайся.",
    "style": "Отвечай по-русски, понятно и по делу. Используй структуру, когда она помогает. Не повторяй вопрос пользователя.",
    "instructions": "Не выдумывай факты и не обещай действий, которые не можешь выполнить. Когда данных недостаточно, честно обозначай неопределённость.",
    "mode": "Авто",
    "online_url": "https://api.openai.com/v1/chat/completions",
    "online_model": "gpt-4.1-mini",
    "online_api_key_protected": "",
    "online_api_key_plain": "",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:3b",
    "temperature": 0.7,
    "theme": "Тёмная",
    "font_family": "Segoe UI",
    "font_size": 12,
    "memory": [],
    "memory_metadata": [],
    "auto_memory": True,
    "memory_semantic_enabled": True,
    "memory_embedding_model": "nomic-embed-text",
    "auto_backup_enabled": True,
    "backup_retention_count": 7,
    "backup_dir": "",
    "backup_cloud_enabled": True,
    "backup_cloud_retention_count": 7,
    "backup_supabase_enabled": False,
    "backup_supabase_retention_count": 7,
    "backup_legacy_migrated": False,
    "voice_tts": False,
    "voice_engine": "system",
    "tts_voice_id": "",
    "tts_rate": 175,
    "microphone_enabled": False,
    "plugins_enabled": False,
    "update_repo": "MrNike227/MoyaIshka",
    "auto_update_check": True,
    "auto_cleanup_old_versions": True,
    "install_dir": "",
    "install_dir_user_selected": False,
    "install_root_dir": "",
    "updates_dir": "",
    "github_pr_repo": "MrNike227/MoyaIshka",
    "github_pr_base": "main",
    "github_pr_token_protected": "",
    "github_pr_token_plain": "",
    "web_search_enabled": True,
    "auto_search_current": True,
    "use_research_library": True,
    "research_auto_enabled": True,
    "research_auto_user_configured": False,
    "research_topics": "Искусственный интеллект и новые модели\nPython и разработка программного обеспечения\nWindows и безопасность компьютера\nНаучно-технические разработки",
    "research_interval_hours": 3,
    "research_results_limit": 10,
    "research_fetch_pages": 5,
    "cloud_primary_provider": "google_drive",
    "google_drive_client_id": "",
    "google_drive_storage_limit_gb": 10,
    "cloud_url": "",
    "cloud_publishable_key": "",
    "cloud_email": "",
    "cloud_auto_sync": True,
    "cloud_auto_sync_user_configured": False,
    "cloud_sync_interval_minutes": 5,
    "cloud_modified_at": "",
    "background_ram_limit_mb": 5120,
    "background_min_available_ram_mb": 8192,
    "background_cpu_limit_percent": 85,
    "developer_architect_model": "",
    "developer_coder_model": "",
    "developer_reviewer_model": "",
}


def resource_path(name: str) -> Path:
    """Locate a bundled asset both in source and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def prepare_source_workspace() -> Path:
    """Expose editable project sources in a persistent folder for packaged builds."""
    if not getattr(sys, "frozen", False):
        source_root = Path(__file__).resolve().parent
        os.environ["ISH_PROJECT_DIR"] = str(source_root)
        os.environ["MOYA_PROJECT_DIR"] = str(source_root)  # legacy plugin compatibility
        return source_root
    container_root = resolve_container_root(
        config=load_json(CONFIG_PATH, {}),
        config_path=CONFIG_PATH,
        running_path=Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None,
        source_root=Path(__file__).resolve().parent,
    )
    source_root = container_root / "Workspaces" / f"source_project_{APP_VERSION}"
    legacy_workspace = DATA_DIR / f"source_project_{APP_VERSION}"
    if not source_root.exists() and legacy_workspace.is_dir():
        source_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy_workspace, source_root, symlinks=True)
        if (source_root / "main.py").is_file():
            shutil.rmtree(legacy_workspace, ignore_errors=True)
    source_root.parent.mkdir(parents=True, exist_ok=True)
    if not (source_root / "main.py").exists():
        bundled_files = [
            "main.py", "conversation_store.py", "secure_storage.py", "app_runtime.py", "backup_interface.py", "chat_interface.py", "cloud_interface.py", "memory_interface.py", "plugin_interface.py", "research_interface.py", "self_improvement_interface.py", "settings_interface.py", "ui_components.py", "update_interface.py", "voice_interface.py", "app_initialization_interface.py", "app_lifecycle_interface.py", "model_settings_interface.py", "ui_pages_interface.py", "ui_theme_interface.py", "temporary_storage.py", "windows_control.py", "voice_assistant.py", "voice_manager.py", "auto_memory.py", "intelligent_memory.py", "backup_manager.py", "backup_cloud.py", "context_engine.py", "improvement_delivery.py", "github_pr.py",
            "avatar_widget.py", "plugin_manager.py", "self_improvement.py", "improvement_testing.py", "windows_sandbox.py", "background_developer.py", "module_dependency_analyzer.py", "updater.py",
            "updater_helper.py", "web_research.py", "cloud_sync.py", "google_drive_sync.py", "resource_governor.py", "supabase_backup.py", "requirements.txt", "requirements_voice.txt", "START.bat",
            "BUILD_EXE.bat", "prepare_python_runtime.py", "verify_pe_dependencies.py", "package_onedir.py", "TEST_WINDOWS10.ps1", "INSTALL_OPTIONAL_DOCS.bat", "INSTALL_VOICE.bat",
            "INSTALLER.iss", "README_RU.md", "SUPABASE_SETUP_RU.md", "GOOGLE_DRIVE_SETUP_RU.md", "CHANGELOG_RU.md", "PLAN_4.2.6_RU.md", "PLAN_4.2.7_RU.md", "PLAN_4.2.8_RU.md", "PLAN_4.2.9_RU.md", "PLAN_4.3.0_RU.md", "PLAN_4.3.1_RU.md", "LICENSE.txt", "THIRD_PARTY_NOTICES.md", "licenses/GPL-3.0.txt",
            "app_icon.ico", "app_icon.png", "app_icon_256.png", "app_icon_128.png", "app_icon_64.png", "app_icon_32.png", "app_icon_16.png",
            "tests/test_core.py", "tests/test_secure_storage.py", "tests/test_interface_modules.py", "tests/test_extensions.py", "tests/test_backup_manager.py", "tests/test_backup_cloud.py", "tests/test_context_engine.py", "tests/test_onedir_update.py", "tests/test_web_research.py", "tests/test_voice_manager.py", "tests/test_cloud_sync.py", "tests/test_resource_governor.py", "tests/test_google_drive_sync.py", "tests/test_build_release.py", "tests/test_improvement_delivery.py", "tests/test_improvement_testing.py", "tests/test_windows_sandbox.py", "tests/test_intelligent_memory.py", "tests/test_supabase_backup.py", "tests/test_background_developer.py", "tests/test_module_dependency_analyzer.py", "tests/test_github_pr.py", "tests/test_multi_file_diff_recovery.py", "tests/test_import_organization.py", "tests/test_temporary_storage.py",
            "hooks/hook-cryptography.py", ".github/workflows/release.yml", "sql/supabase_setup.sql",
        ]
        for relative in bundled_files:
            src = resource_path(f"source_project/{relative}")
            if src.is_file():
                dest = source_root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    os.environ["ISH_PROJECT_DIR"] = str(source_root)
    os.environ["MOYA_PROJECT_DIR"] = str(source_root)  # legacy plugin compatibility
    return source_root


def atomic_json_save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 180) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", "User-Agent": f"ISH/{APP_VERSION}"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Нет соединения с сервером: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Сервер слишком долго не отвечает. Попробуйте ещё раз.") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Сервер вернул некорректный JSON. Проверьте адрес API.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Сервер вернул неожиданный формат ответа.")
    return result


def extract_openai_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Неожиданный ответ API. Проверьте совместимость провайдера с Chat Completions.") from exc
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
    return str(content).strip()


def read_attachment(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".py", ".log", ".xml", ".html", ".htm", ".yaml", ".yml", ".ini", ".sql"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Для чтения PDF установите пакет: py -m pip install pypdf") from exc
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix == ".docx":
        try:
            from docx import Document  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Для чтения DOCX установите пакет: py -m pip install python-docx") from exc
        document = Document(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    else:
        raise RuntimeError("Этот тип файла пока не поддерживается. Используйте TXT, MD, CSV, JSON, PDF или DOCX.")
    if not text.strip():
        raise RuntimeError("В файле не найден текст. Возможно, PDF состоит из сканов.")
    if len(text) > MAX_ATTACHMENT_CHARS:
        text = text[:MAX_ATTACHMENT_CHARS] + "\n\n[Текст сокращён: достигнут лимит вложения.]"
    return text


class ISHApp(
    _app_initialization_interface.ApplicationInitializationMixin,
    _ui_theme_interface.UIThemeMixin,
    _ui_pages_interface.UIPageMixin,
    _chat_interface.ChatInterfaceMixin,
    _research_interface.ResearchInterfaceMixin,
    _cloud_interface.CloudInterfaceMixin,
    _settings_interface.SettingsInterfaceMixin,
    _backup_interface.BackupInterfaceMixin,
    _voice_interface.VoiceInterfaceMixin,
    _memory_interface.MemoryInterfaceMixin,
    _plugin_interface.PluginInterfaceMixin,
    _self_improvement_interface.SelfImprovementInterfaceMixin,
    _update_interface.UpdateInterfaceMixin,
    _model_settings_interface.ModelSettingsMixin,
    _app_lifecycle_interface.AppLifecycleMixin,
):
    """Backward-compatible public application class; implementations live in feature mixins."""
    pass
def migrate_staged_exe_on_first_launch() -> bool:
    """Install an EXE launched from update_staging into the chosen permanent folder.

    Older releases downloaded the new EXE into AppData and sometimes launched it
    directly. Do not try to overwrite the destination from this still-running EXE:
    Windows locks the image file. Instead, start the detached PowerShell installer,
    exit this process, and let the helper replace the target after both old instances
    have closed. The helper verifies SHA-256, embedded version, actual launch path,
    and the new process' startup receipt before reporting success.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        running_exe = Path(sys.executable).resolve()
        if running_exe.parent.name.casefold() != "update_staging":
            return False

        saved_config = load_json(CONFIG_PATH, {})
        if not isinstance(saved_config, dict):
            saved_config = {}
        was_user_selected = bool(saved_config.get("install_dir_user_selected", False))
        configured_root = str(saved_config.get("install_root_dir", "")).strip()
        container_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else default_install_root(
                legacy_install_dir=saved_config.get("install_dir"),
                running_exe=(
                    None if running_exe.parent.name.casefold() == "update_staging"
                    else running_exe
                ),
            )
        )

        if not was_user_selected and not configured_root:
            chooser_root = tk.Tk()
            chooser_root.withdraw()
            try:
                selected = filedialog.askdirectory(
                    parent=chooser_root,
                    title="Выбери корневую папку контейнера (в ней будут App и Updates)",
                    initialdir=str(container_root),
                    mustexist=False,
                )
            finally:
                chooser_root.destroy()
            if not selected:
                # Keep the staged app usable; the user can set the target in Settings.
                return False
            container_root = Path(selected).expanduser().resolve()

        layout = ensure_install_layout(container_root)
        target_dir = ensure_install_dir_writable(layout["app"])
        ensure_install_dir_writable(layout["updates"])
        target_exe = target_dir / "ISH.exe"
        saved_config["install_root_dir"] = str(layout["root"])
        saved_config["updates_dir"] = str(layout["updates"])
        saved_config["install_dir"] = str(target_dir)
        saved_config["install_dir_user_selected"] = True
        atomic_json_save(CONFIG_PATH, saved_config)
        configure_temporary_storage(
            config=saved_config,
            config_path=CONFIG_PATH,
            running_path=running_exe,
        )

        # This process is the staged EXE. The helper waits for it to exit, then
        # atomically replaces target_exe and launches the verified new process.
        launch_windows_replacement(
            staged_path=running_exe,
            target_path=target_exe,
            parent_pid=os.getpid(),
            log_path=DATA_DIR / "update.log",
            expected_version=APP_VERSION,
            receipt_path=get_temp_directory("updates") / "update_confirmation.json",
        )
        return True
    except Exception as exc:
        try:
            messagebox.showerror(
                "Установка Моей ИИшки",
                f"Не удалось запустить безопасную установку из временной папки.\n\nПричина: {exc}\n\nСкачанный EXE сохранён здесь:\n{Path(sys.executable).resolve()}\n\nЖурнал установки:\n{DATA_DIR / 'update.log'}",
            )
        except Exception:
            pass
        return False



def confirm_update_startup(receipt_path: str, expected_version: str, expected_sha256: str, expected_target: str) -> tuple[bool, str]:
    """Confirm an updater launch only when version, executable path and hash match."""
    receipt = Path(receipt_path).expanduser().resolve()
    target = Path(expected_target).expanduser().resolve()
    try:
        actual_exe = Path(sys.executable).resolve()
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Запустилась не автономная EXE-сборка.")
        if APP_VERSION != expected_version:
            raise RuntimeError(f"Запущена версия {APP_VERSION}, ожидалась {expected_version}.")
        if actual_exe != target:
            raise RuntimeError(f"Приложение запущено из {actual_exe}, ожидался путь {target}.")
        actual_hash = sha256_file(actual_exe)
        if actual_hash.lower() != expected_sha256.lower():
            raise RuntimeError("SHA-256 запущенного EXE не совпадает с загруженным файлом.")
        result = {
            "confirmed": True,
            "version": APP_VERSION,
            "target": str(actual_exe),
            "sha256": actual_hash.lower(),
            "message": "Новая версия успешно запущена и проверена.",
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        atomic_json_save(receipt, result)
        return True, str(actual_exe)
    except Exception as exc:
        try:
            atomic_json_save(receipt, {
                "confirmed": False,
                "version": APP_VERSION,
                "target": str(Path(sys.executable).resolve()),
                "sha256": "",
                "message": str(exc),
                "timestamp": datetime.now().astimezone().isoformat(),
            })
        except Exception:
            pass
        return False, str(exc)


def _handle_update_confirmation_args(root: tk.Tk) -> None:
    """Process the private command line handshake passed by the Windows updater."""
    confirmation = None
    if len(sys.argv) >= 6 and sys.argv[1] == "--confirm-update":
        confirmation = tuple(sys.argv[2:6])
    elif all(os.environ.get(key) for key in (
        "MOYA_UPDATE_RECEIPT", "MOYA_UPDATE_VERSION",
        "MOYA_UPDATE_SHA256", "MOYA_UPDATE_TARGET",
    )):
        confirmation = (
            os.environ["MOYA_UPDATE_RECEIPT"],
            os.environ["MOYA_UPDATE_VERSION"],
            os.environ["MOYA_UPDATE_SHA256"],
            os.environ["MOYA_UPDATE_TARGET"],
        )
    if confirmation:
        receipt, version, expected_hash, target = confirmation

        def confirm() -> None:
            ok, detail = confirm_update_startup(receipt, version, expected_hash, target)
            if ok:
                messagebox.showinfo(
                    "Обновление подтверждено",
                    f"ISH версии {version} успешно установлена и запущена.\n\nФактический путь:\n{detail}",
                    parent=root,
                )

                # Search for legacy copies only after the updater has received a
                # valid startup receipt. A short delay lets its final backup cleanup
                # finish before this process inspects neighboring install folders.
                def start_old_version_cleanup() -> None:
                    def worker() -> None:
                        settings = load_json(CONFIG_PATH, DEFAULT_CONFIG)
                        if not isinstance(settings, dict) or not bool(
                            settings.get("auto_cleanup_old_versions", True)
                        ):
                            return
                        try:
                            result = cleanup_old_application_copies(
                                configured_install_dir=settings.get("install_dir") or Path(sys.executable).resolve().parent,
                                current_exe=Path(sys.executable).resolve(),
                                data_dir=DATA_DIR,
                            )
                            with (DATA_DIR / "update.log").open("a", encoding="utf-8") as log:
                                log.write(
                                    f"{datetime.now().astimezone().isoformat()}  "
                                    f"Автоочистка старых копий: найдено {result['found']}, "
                                    f"удалено {result['deleted']}, ошибок {len(result['errors'])}.\n"
                                )
                                for error in result["errors"][:10]:
                                    log.write(f"  {error}\n")
                        except Exception as exc:
                            try:
                                with (DATA_DIR / "update.log").open("a", encoding="utf-8") as log:
                                    log.write(f"{datetime.now().astimezone().isoformat()}  Ошибка автоочистки: {exc}\n")
                            except OSError:
                                pass

                    threading.Thread(target=worker, daemon=True).start()

                root.after(5000, start_old_version_cleanup)
            else:
                messagebox.showerror(
                    "Проверка обновления не пройдена",
                    f"Новая версия не прошла проверку. Установщик восстановит предыдущую версию.\n\nПричина: {detail}",
                    parent=root,
                )

        root.after(1200, confirm)
        return

    # If the helper rolled back, it leaves a failed receipt for the restored app.
    failure_receipt = get_temp_directory("updates") / "update_confirmation.json"
    previous = load_json(failure_receipt, {})
    if isinstance(previous, dict) and previous.get("confirmed") is False:
        try:
            failure_receipt.unlink(missing_ok=True)
        except OSError:
            pass
        reason = str(previous.get("message", "Неизвестная причина"))
        log_path = DATA_DIR / "update.log"

        def show_failure() -> None:
            messagebox.showwarning(
                "Обновление отменено — восстановлена предыдущая версия",
                f"Новая версия не прошла проверку. Предыдущая версия восстановлена.\n\nПричина: {reason}\n\nЖурнал установки:\n{log_path}",
                parent=root,
            )
        root.after(1200, show_failure)


def _packaged_import_self_test() -> int:
    """Fast, no-GUI smoke test for bundled native runtime and crypto imports."""
    try:
        # Do not let the CI runner's installed VC++ runtime mask missing files
        # in the one-file archive: verify the app-local runtime was extracted.
        if getattr(sys, "frozen", False):
            runtime_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent / "_internal"))
            required_runtime = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
            missing_runtime = [name for name in required_runtime if not (runtime_root / name).is_file()]
            if missing_runtime:
                return 73

        # Force PyInstaller to include CFFI's native backend. Some runtime
        # dependencies load it dynamically, so static analysis can miss it.
        import cffi as _cffi  # noqa: F401
        import _cffi_backend  # noqa: F401

        salt = b"ISH-test-salt"
        key = _CryptoScrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(b"packaging-smoke-test")
        cipher = _CryptoAESGCM(key)
        nonce = b"\x01" * 12
        smoke_aad = f"{APP_VERSION}-smoke".encode("ascii")
        ciphertext = cipher.encrypt(nonce, b"ISH cryptography bundle OK", smoke_aad)
        plaintext = cipher.decrypt(nonce, ciphertext, smoke_aad)
        if plaintext != b"ISH cryptography bundle OK":
            return 71
        return 0
    except Exception:
        return 72


def main() -> None:
    try:
        startup_config = load_json(CONFIG_PATH, {})
        if not isinstance(startup_config, dict):
            startup_config = {}
        running_exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
        if not str(startup_config.get("install_root_dir", "")).strip():
            startup_config["install_root_dir"] = str(
                default_install_root(
                    legacy_install_dir=startup_config.get("install_dir"),
                    running_exe=running_exe,
                )
            )
        configure_temporary_storage(
            config=startup_config,
            config_path=CONFIG_PATH,
            running_path=running_exe,
            source_root=Path(__file__).resolve().parent,
        )
    except Exception as exc:
        try:
            messagebox.showerror(
                "Временная папка контейнера",
                f"Не удалось подготовить временную папку на выбранном диске. "
                f"Системная папка TEMP не будет использоваться.\\n\\n{exc}",
            )
        except Exception:
            pass
        return
    if os.name == "nt":
        try:
            # A stable AppUserModelID helps Windows associate the high-resolution
            # executable/window icon with the same taskbar application across updates.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MrNike227.ISH.Desktop")
        except Exception:
            pass
    if migrate_staged_exe_on_first_launch():
        return
    prepare_source_workspace()
    root = tk.Tk()
    ISHApp(root)
    _handle_update_confirmation_args(root)
    root.mainloop()

# Bind completed main.py globals to the feature mixins.
_app_runtime.bind_main_globals(
    globals(),
    _app_initialization_interface, _app_lifecycle_interface,
    _backup_interface, _chat_interface, _cloud_interface, _memory_interface,
    _model_settings_interface, _plugin_interface, _research_interface,
    _self_improvement_interface, _settings_interface, _ui_pages_interface,
    _ui_theme_interface, _update_interface, _voice_interface,
)

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test-imports":
        raise SystemExit(_packaged_import_self_test())
    if len(sys.argv) >= 4 and sys.argv[1] == "--apply-update":
        from updater_helper import apply_update
        raise SystemExit(apply_update(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "0"))
    main()
