# -*- coding: utf-8 -*-
"""Verified ZIP updater for the PyInstaller onedir distribution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_PACKAGE_FILES = 20000
DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
FILE_HASH_CHUNK_BYTES = 4 * 1024 * 1024
EXTRACT_CHUNK_BYTES = 4 * 1024 * 1024
_RETRYABLE_WINDOWS_LOCK_ERRORS = {5, 32, 33}


def _replace_with_retry(
    source: Path,
    destination: Path,
    *,
    attempts: int = 12,
    initial_delay: float = 0.25,
) -> None:
    """Atomically rename a closed file, retrying transient Windows AV/indexer locks."""
    last_error: OSError | None = None
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            last_error = exc
            code = getattr(exc, "winerror", None) or exc.errno
            if code not in _RETRYABLE_WINDOWS_LOCK_ERRORS or attempt + 1 >= attempts:
                raise
            time.sleep(min(max(0.05, initial_delay) * (2 ** min(attempt, 4)), 1.5))
    if last_error is not None:
        raise last_error


def _unlink_best_effort(path: Path, *, attempts: int = 5) -> bool:
    """Remove a temporary updater file without masking the original failure."""
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            code = getattr(exc, "winerror", None) or exc.errno
            if code not in _RETRYABLE_WINDOWS_LOCK_ERRORS or attempt + 1 >= attempts:
                return False
            time.sleep(0.15 * (attempt + 1))
    return False


def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MoyaIshka-Updater/4", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def _download_to(url: str, destination: Path, max_bytes: int = MAX_PACKAGE_BYTES) -> tuple[str, int]:
    """Stream package bytes to disk and hash incrementally instead of holding a ZIP in RAM."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MoyaIshka-Updater/4", "Accept": "application/octet-stream"},
    )
    digest = hashlib.sha256()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation makes accidental path reuse/concurrent downloads fail safely.
        with urllib.request.urlopen(request, timeout=90) as response, destination.open("xb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    announced = int(content_length)
                except (TypeError, ValueError):
                    announced = 0
                if announced > max_bytes:
                    raise RuntimeError("Размер пакета обновления превышает допустимый лимит.")
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("Размер пакета обновления превышает допустимый лимит.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total <= 0:
            raise RuntimeError("Сервер вернул пустой пакет обновления.")
        return digest.hexdigest(), total
    except Exception:
        _unlink_best_effort(destination)
        raise


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+){1,3})", value)
    if not match:
        return (0, 0, 0, 0)
    parts = [int(part) for part in match.group(1).split(".")]
    return tuple((parts + [0, 0, 0, 0])[:4])


def check_latest_release(repository: str, current_version: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository.strip()):
        raise ValueError("Укажите репозиторий в формате владелец/название, например myname/moya-ishka")
    data = json.loads(_get(f"https://api.github.com/repos/{repository}/releases/latest").decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    assets = data.get("assets", [])
    package = next((a for a in assets if str(a.get("name", "")).lower() == "moyaishka.zip"), None)
    checksum = next((a for a in assets if str(a.get("name", "")).lower() == "moyaishka.zip.sha256"), None)
    return {
        "version": tag.lstrip("v"),
        "url": data.get("html_url", ""),
        "body": str(data.get("body", ""))[:4000],
        "available": version_tuple(tag) > version_tuple(current_version),
        "package_url": package.get("browser_download_url") if package else None,
        "checksum_url": checksum.get("browser_download_url") if checksum else None,
        "asset_name": package.get("name") if package else None,
    }


def _safe_member_path(name: str) -> tuple[str, ...]:
    raw = name.replace("\\", "/")
    if raw.startswith("/") or "\x00" in raw or ":" in raw:
        raise RuntimeError("Архив содержит абсолютный или недопустимый путь.")
    parts = tuple(part for part in raw.rstrip("/").split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise RuntimeError("Архив содержит небезопасный путь.")
    if parts[0].casefold() != "moyaishka":
        raise RuntimeError("Архив должен содержать только корневую папку MoyaIshka.")
    return parts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(FILE_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_app_folder(app_dir: Path, expected_version: str | None = None) -> dict[str, Any]:
    root = app_dir.resolve()
    try:
        manifest = json.loads((root / "build_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Манифест сборки отсутствует или повреждён: {exc}") from exc
    version = str(manifest.get("version", "")).strip()
    if not version or (expected_version and version != expected_version.lstrip("v")):
        raise RuntimeError(f"Версия пакета не совпадает: {version or 'не указана'}.")
    if manifest.get("entrypoint") != "MoyaIshka.exe":
        raise RuntimeError("Неверная точка входа пакета обновления.")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_PACKAGE_FILES:
        raise RuntimeError("Манифест содержит неверный список файлов.")
    seen: set[str] = set()
    exe_hash = ""
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Повреждён элемент манифеста.")
        relative = PurePosixPath(str(entry.get("path", "")).replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or any(
            part in {"", ".", ".."} or ":" in part for part in relative.parts
        ):
            raise RuntimeError("Манифест содержит небезопасный путь.")
        key = relative.as_posix().casefold()
        if key in seen:
            raise RuntimeError(f"Повтор файла в манифесте: {relative}.")
        seen.add(key)
        path = root.joinpath(*relative.parts).resolve()
        if root not in path.parents or not path.is_file():
            raise RuntimeError(f"Файл пакета отсутствует или выходит за пределы папки: {relative}.")
        if path.stat().st_size != int(entry.get("size", -1)):
            raise RuntimeError(f"Размер файла пакета не совпадает: {relative}.")
        expected_hash = str(entry.get("sha256", "")).lower()
        actual_hash = _sha256_file(path)
        if not re.fullmatch(r"[a-f0-9]{64}", expected_hash) or actual_hash != expected_hash:
            raise RuntimeError(f"Контрольная сумма файла пакета не совпадает: {relative}.")
        if key == "moyaishka.exe":
            exe_hash = actual_hash
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
    if not exe_hash or exe_hash.lower() != str(manifest.get("exe_sha256", "")).lower():
        raise RuntimeError("SHA-256 главного EXE не совпадает с манифестом.")
    for name in ("python311.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        if not (root / "_internal" / name).is_file():
            raise RuntimeError(f"В пакете отсутствует _internal/{name}.")
    return manifest


def _extract_package(archive: Path, destination: Path, expected_version: str) -> Path:
    extracting = destination / "MoyaIshka.extracting"
    app_dir = extracting / "MoyaIshka"
    final_dir = destination / "MoyaIshka"
    if extracting.exists():
        shutil.rmtree(extracting)
    extracting.mkdir(parents=True)
    names: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if not members or len(members) > MAX_PACKAGE_FILES:
                raise RuntimeError("Архив обновления пуст или содержит слишком много файлов.")
            if sum(item.file_size for item in members) > MAX_PACKAGE_BYTES:
                raise RuntimeError("Размер распакованного обновления превышает 2 ГБ.")
            for item in members:
                parts = _safe_member_path(item.filename)
                key = "/".join(parts).casefold()
                if key in names:
                    raise RuntimeError(f"В архиве повторяется путь: {item.filename}")
                names.add(key)
                mode = (item.external_attr >> 16) & 0xFFFF
                if (mode & 0o170000) not in (0, 0o040000, 0o100000):
                    raise RuntimeError("Архив содержит неподдерживаемый тип файла.")
                target = extracting.joinpath(*parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=EXTRACT_CHUNK_BYTES)
        validate_app_folder(app_dir, expected_version)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        app_dir.replace(final_dir)
        return final_dir / "MoyaIshka.exe"
    finally:
        if extracting.exists():
            shutil.rmtree(extracting, ignore_errors=True)


def download_verified_update(info: dict[str, Any], target_dir: Path) -> Path:
    """Download each update into a private staging directory.

    Unique per-attempt paths prevent two update clicks from opening/replacing the
    same .tmp file. The temporary file is closed and flushed before rename; a
    bounded retry handles brief Windows Defender/indexer sharing violations.
    """
    if not info.get("package_url") or not info.get("checksum_url"):
        raise RuntimeError("В релизе не найдены MoyaIshka.zip и MoyaIshka.zip.sha256.")
    checksum_text = _get(str(info["checksum_url"])).decode("utf-8", errors="replace").strip()
    match = re.search(r"\b([a-fA-F0-9]{64})\b", checksum_text)
    if not match:
        raise RuntimeError("Файл контрольной суммы имеет неверный формат.")
    target_dir.mkdir(parents=True, exist_ok=True)
    # Keep every attempt isolated: no shared archive, .tmp file, or extraction folder.
    attempt_dir = target_dir / f"MoyaIshka_update_{uuid.uuid4().hex}"
    attempt_dir.mkdir(parents=False, exist_ok=False)
    archive = attempt_dir / "MoyaIshka_update.zip"
    temporary = attempt_dir / "MoyaIshka_update.part"
    try:
        actual, _ = _download_to(str(info["package_url"]), temporary, MAX_PACKAGE_BYTES)
        if actual.lower() != match.group(1).lower():
            raise RuntimeError("Контрольная сумма пакета не совпадает. Обновление отклонено.")
        # Both handles in _download_to have exited before this atomic rename.
        _replace_with_retry(temporary, archive)
        staged_exe = _extract_package(archive, attempt_dir, str(info.get("version", "")))
        _unlink_best_effort(archive)
        return staged_exe
    except Exception:
        # Never delete another update attempt's files. A locked temp is harmless
        # and uniquely named; cleanup is best effort so it cannot hide the cause.
        shutil.rmtree(attempt_dir, ignore_errors=True)
        raise
