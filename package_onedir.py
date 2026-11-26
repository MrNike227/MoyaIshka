#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a SHA-256-manifested ZIP from the PyInstaller onedir output."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_package(dist_dir: Path, output: Path, version: str) -> Path:
    root = dist_dir.resolve()
    if not (root / "MoyaIshka.exe").is_file():
        raise FileNotFoundError(f"Не найден {root / 'MoyaIshka.exe'}")
    for name in ("python311.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        if not (root / "_internal" / name).is_file():
            raise RuntimeError(f"В onedir-папке отсутствует _internal/{name}")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Символическая ссылка запрещена в пакете: {path}")
        if path.is_file() and path.name != "build_manifest.json":
            files.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    if not files or len(files) > 20000:
        raise RuntimeError("Неверное количество файлов в onedir-пакете.")
    exe_hash = next(entry["sha256"] for entry in files if entry["path"] == "MoyaIshka.exe")
    manifest = {
        "schema": 1,
        "version": version,
        "entrypoint": "MoyaIshka.exe",
        "exe_sha256": exe_hash,
        "files": files,
    }
    (root / "build_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        archive.writestr("MoyaIshka/", "")
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, "MoyaIshka/" + path.relative_to(root).as_posix())
    temporary.replace(output)
    Path(str(output) + ".sha256").write_text(
        f"{sha256_file(output)}  {output.name}\n", encoding="ascii"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = build_package(args.dist_dir, args.output, args.version)
    print(f"Пакет создан: {result}")
    print(f"SHA-256: {sha256_file(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
