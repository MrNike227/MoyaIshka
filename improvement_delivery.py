# -*- coding: utf-8 -*-
"""Safe delivery of self-improvement proposals outside the replaceable app folder."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

MAX_PROPOSAL_FILES = 10
MAX_PROPOSAL_FILE_BYTES = 2 * 1024 * 1024
MAX_PROPOSAL_TOTAL_BYTES = 8 * 1024 * 1024


def _safe_relative_path(value: str | Path) -> PurePosixPath:
    raw = str(value).replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
        or path.parts[0].casefold() in {".git", ".github"}
        or path.suffix.casefold() != ".py"
    ):
        raise ValueError("Разрешено сохранять только безопасные относительные пути к Python-файлам проекта.")
    return path


def resolve_install_layout(root_dir: str | Path) -> dict[str, Path]:
    """Return sibling App and Updates directories under one stable container root."""
    root = Path(root_dir).expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("Нельзя использовать корень диска как папку контейнера.")
    app = (root / "App").resolve()
    updates = (root / "Updates").resolve()
    if app == updates or app in updates.parents or updates in app.parents:
        raise ValueError("Папки App и Updates должны быть соседними и независимыми.")
    return {"root": root, "app": app, "updates": updates}


def default_install_root(
    configured_root: str | Path | None = None,
    legacy_install_dir: str | Path | None = None,
    running_exe: str | Path | None = None,
) -> Path:
    """Suggest a new container without moving or overwriting an existing installation."""
    if configured_root and str(configured_root).strip():
        return Path(os.path.expandvars(str(configured_root).strip())).expanduser().resolve()
    legacy = Path(legacy_install_dir).expanduser().resolve() if legacy_install_dir else None
    if legacy is None and running_exe:
        legacy = Path(running_exe).expanduser().resolve().parent
    if legacy is None:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        legacy = base / "Programs" / "MoyaIshka"
    if legacy.name.casefold() == "app":
        return legacy.parent
    # Keep an existing legacy installation untouched until the user confirms migration.
    return legacy.parent / "MoyaIshka_Container"


def resolve_updates_directory(
    configured_updates_dir: str | Path | None = None,
    install_root: str | Path | None = None,
    app_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> Path:
    """Resolve Updates and guarantee it is never inside the folder replaced by the updater."""
    if configured_updates_dir and str(configured_updates_dir).strip():
        updates = Path(os.path.expandvars(str(configured_updates_dir).strip())).expanduser().resolve()
    elif install_root:
        updates = resolve_install_layout(install_root)["updates"]
    elif app_dir:
        updates = Path(app_dir).expanduser().resolve().parent / "Updates"
    elif data_dir:
        updates = Path(data_dir).expanduser().resolve() / "Updates"
    else:
        raise ValueError("Не задана безопасная папка для предложений обновлений.")
    if app_dir:
        app = Path(app_dir).expanduser().resolve()
        if updates == app or app in updates.parents:
            raise ValueError("Папка Updates не может находиться внутри заменяемой папки App.")
    return updates


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_change_bundle(
    updates_dir: str | Path,
    relative_path: str | Path,
    original_text: str,
    updated_text: str,
    patch_text: str,
    *,
    title: str = "Предложение саморазвития",
    version: str = "",
) -> Path:
    """Persist a review-only proposal with the full changed file, patch and checksums."""
    relative = _safe_relative_path(relative_path)
    if not isinstance(original_text, str) or not isinstance(updated_text, str) or not isinstance(patch_text, str):
        raise TypeError("Содержимое предложения должно быть текстом.")
    original_bytes = original_text.encode("utf-8")
    updated_bytes = updated_text.encode("utf-8")
    patch_bytes = patch_text.encode("utf-8")
    if len(updated_bytes) > MAX_PROPOSAL_FILE_BYTES or len(patch_bytes) > MAX_PROPOSAL_FILE_BYTES:
        raise ValueError("Файл предложения слишком большой (максимум 2 МБ).")
    if len(original_bytes) + len(updated_bytes) + len(patch_bytes) > MAX_PROPOSAL_TOTAL_BYTES:
        raise ValueError("Пакет предложения превышает 8 МБ.")
    compile(updated_text, relative.as_posix(), "exec")

    root = Path(updates_dir).expanduser().resolve() / "Proposals"
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bundle = root / f"{timestamp}_{uuid.uuid4().hex[:8]}"
    staging = root / f".{bundle.name}.staging"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        file_path = staging / "files" / Path(*relative.parts)
        _write_text_atomic(file_path, updated_text)
        _write_text_atomic(staging / "changes.patch", patch_text.rstrip() + "\n")
        manifest: dict[str, Any] = {
            "schema": 1,
            "status": "review_only_not_applied",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": str(version)[:40],
            "title": str(title).strip()[:200] or "Предложение саморазвития",
            "files": [{
                "path": relative.as_posix(),
                "size": len(updated_bytes),
                "sha256": hashlib.sha256(updated_bytes).hexdigest(),
                "base_sha256": hashlib.sha256(original_bytes).hexdigest(),
            }],
            "patch": {
                "path": "changes.patch",
                "size": len(patch_bytes),
                "sha256": hashlib.sha256(patch_bytes).hexdigest(),
            },
        }
        _write_text_atomic(
            staging / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        os.replace(staging, bundle)
        return bundle
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
