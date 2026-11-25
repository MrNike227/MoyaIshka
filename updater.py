# -*- coding: utf-8 -*-
"""GitHub Releases update checker with SHA-256 verification."""
from __future__ import annotations
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any


def _get(url: str, binary: bool = False) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MoyaIshka-Updater/3", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


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
    exe_asset = next((a for a in assets if str(a.get("name", "")).lower() == "моя иишка.exe" or str(a.get("name", "")).lower() == "moyaishka.exe"), None)
    checksum_asset = next((a for a in assets if str(a.get("name", "")).lower() in {"moyaishka.exe.sha256", "моя иишка.exe.sha256"}), None)
    return {
        "version": tag.lstrip("v"),
        "url": data.get("html_url", ""),
        "body": str(data.get("body", ""))[:4000],
        "available": version_tuple(tag) > version_tuple(current_version),
        "exe_url": exe_asset.get("browser_download_url") if exe_asset else None,
        "checksum_url": checksum_asset.get("browser_download_url") if checksum_asset else None,
        "asset_name": exe_asset.get("name") if exe_asset else None,
    }


def download_verified_update(info: dict[str, Any], target_dir: Path) -> Path:
    if not info.get("exe_url") or not info.get("checksum_url"):
        raise RuntimeError("В релизе не найдены Моя ИИшка.exe и Моя ИИшка.exe.sha256. Сборка релиза должна приложить оба файла.")
    binary = _get(str(info["exe_url"]), binary=True)
    checksum_text = _get(str(info["checksum_url"])).decode("utf-8", errors="replace").strip()
    match = re.search(r"\b([a-fA-F0-9]{64})\b", checksum_text)
    if not match:
        raise RuntimeError("Файл контрольной суммы имеет неверный формат.")
    actual = hashlib.sha256(binary).hexdigest()
    if actual.lower() != match.group(1).lower():
        raise RuntimeError("Контрольная сумма не совпадает. Обновление отклонено.")
    target_dir.mkdir(parents=True, exist_ok=True)
    staged = target_dir / "MoyaIshka_update.exe"
    temporary = staged.with_suffix(".tmp")
    temporary.write_bytes(binary)
    temporary.replace(staged)
    return staged
