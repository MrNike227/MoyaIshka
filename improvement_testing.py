# -*- coding: utf-8 -*-
"""Test self-improvement proposals in a disposable project copy."""
from __future__ import annotations
import hashlib, json, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from improvement_delivery import _safe_relative_path, _write_text_atomic
from self_improvement import apply_unified_diff

class ProposalTestError(RuntimeError):
    """Proposal integrity or test execution error."""

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _normalized_text_sha256(data: bytes) -> str:
    """Hash UTF-8 source text independently of Windows/Linux newline encoding."""
    text = data.decode("utf-8")
    return _sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))

def test_change_bundle(bundle_dir: str | Path, source_root: str | Path, *, timeout_seconds: int = 240) -> dict[str, Any]:
    """Verify a saved proposal, then run unit tests against it in a temporary project copy."""
    bundle, source = Path(bundle_dir).expanduser().resolve(), Path(source_root).expanduser().resolve()
    if not bundle.is_dir() or not source.is_dir():
        raise ProposalTestError("Не найдена папка предложения или исходников.")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 128 * 1024:
        raise ProposalTestError("Манифест предложения отсутствует, слишком велик или является ссылкой.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProposalTestError(f"Манифест предложения повреждён: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ProposalTestError("Манифест предложения имеет неверный формат.")
    entries = manifest.get("files")
    if manifest.get("schema") != 1 or not isinstance(entries, list) or not entries or len(entries) > 10:
        raise ProposalTestError("Неподдерживаемый формат или пустой список файлов.")
    patch_meta = manifest.get("patch")
    if not isinstance(patch_meta, dict) or patch_meta.get("path") != "changes.patch":
        raise ProposalTestError("В манифесте отсутствует корректная ссылка на diff.")
    patch_path = (bundle / "changes.patch").resolve()
    if bundle not in patch_path.parents or not patch_path.is_file():
        raise ProposalTestError("Файл diff предложения не найден.")
    if patch_path.stat().st_size > 2 * 1024 * 1024:
        raise ProposalTestError("Размер diff превышает безопасный лимит 2 МБ.")
    patch_bytes = patch_path.read_bytes()
    if len(patch_bytes) != patch_meta.get("size") or _sha256(patch_bytes) != patch_meta.get("sha256"):
        raise ProposalTestError("Контрольная сумма diff не совпадает.")
    try:
        patch_text = patch_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProposalTestError("Файл diff не является текстом UTF-8.") from exc
    checked, total = [], 0
    for entry in entries:
        try:
            relative = _safe_relative_path(entry.get("path", ""))
        except Exception as exc:
            raise ProposalTestError(str(exc)) from exc
        original, candidate = (source / Path(*relative.parts)).resolve(), (bundle / "files" / Path(*relative.parts)).resolve()
        if source not in original.parents or bundle not in candidate.parents or not original.is_file() or not candidate.is_file():
            raise ProposalTestError(f"Не найден исходный или изменённый файл: {relative.as_posix()}")
        old, new = original.read_bytes(), candidate.read_bytes()
        total += len(new)
        if len(new) > 2 * 1024 * 1024 or total > 8 * 1024 * 1024:
            raise ProposalTestError("Размер пакета превышает безопасный лимит.")
        if _sha256(new) != entry.get("sha256"):
            raise ProposalTestError(f"SHA-256 изменённого файла не совпадает: {relative.as_posix()}")
        try:
            source_hash = _normalized_text_sha256(old)
        except UnicodeDecodeError as exc:
            raise ProposalTestError(f"Исходник {relative.as_posix()} не является UTF-8.") from exc
        if source_hash != entry.get("base_sha256"):
            raise ProposalTestError(f"Исходник {relative.as_posix()} уже изменился. Создай предложение заново.")
        try:
            compile(new.decode("utf-8"), relative.as_posix(), "exec")
        except Exception as exc:
            raise ProposalTestError(f"Ошибка синтаксиса {relative.as_posix()}: {exc}") from exc
        checked.append((relative, candidate, old, new))
    patch_matches = []
    for relative, _candidate, original_bytes, updated_bytes in checked:
        try:
            original_text = original_bytes.decode("utf-8")
            updated_text = updated_bytes.decode("utf-8")
            reconstructed = apply_unified_diff(
                original_text, patch_text, relative.as_posix(),
            )
        except (UnicodeDecodeError, ValueError):
            continue
        normalize = lambda value: value.replace("\\r\\n", "\\n").replace("\\r", "\\n")
        if normalize(reconstructed) == normalize(updated_text):
            patch_matches.append(relative.as_posix())
    if len(patch_matches) != 1:
        raise ProposalTestError(
            "Сохранённый diff не соответствует ровно одному изменённому файлу. "
            "Пакет предложения повреждён или diff устарел."
        )
    report: dict[str, Any] = {
        "schema": 1, "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": False, "checked_files": len(checked), "tests_exit_code": None,
        "stdout": "", "stderr": "", "error": "",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="moya_proposal_test_") as temporary:
            sandbox = Path(temporary) / "project"
            shutil.copytree(source, sandbox, ignore=shutil.ignore_patterns(
                ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                "build", "dist", "*.pyc", "*.db", "*.sqlite",
            ))
            for relative, candidate, _original, _updated in checked:
                destination = sandbox / Path(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination)
            if not (sandbox / "tests").is_dir():
                raise ProposalTestError("В проекте не найдена папка tests.")
            if getattr(sys, "frozen", False):
                runner_result = Path(temporary) / "runner_result.json"
                process = subprocess.run(
                    [sys.executable, "--run-proposal-tests", str(sandbox), str(runner_result)],
                    cwd=sandbox, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=max(30, min(int(timeout_seconds), 600)),
                )
                runner_data = json.loads(runner_result.read_text(encoding="utf-8")) if runner_result.is_file() else {}
                report.update(
                    tests_exit_code=process.returncode,
                    stdout=str(runner_data.get("stdout", process.stdout))[-12000:],
                    stderr=str(runner_data.get("stderr", process.stderr))[-12000:],
                    passed=process.returncode == 0 and bool(runner_data.get("passed")),
                )
            else:
                process = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
                    cwd=sandbox, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=max(30, min(int(timeout_seconds), 600)),
                )
                report.update(
                    tests_exit_code=process.returncode, stdout=process.stdout[-12000:],
                    stderr=process.stderr[-12000:], passed=process.returncode == 0,
                )
    except subprocess.TimeoutExpired as exc:
        report["error"] = f"Превышено время выполнения тестов ({exc.timeout} секунд)."
        report["stdout"] = str(exc.stdout or "")[-12000:]
        report["stderr"] = str(exc.stderr or "")[-12000:]
    except ProposalTestError:
        raise
    except Exception as exc:
        report["error"] = str(exc)
    report_path = bundle / "test_report.json"
    _write_text_atomic(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    manifest["test"] = {
        "status": "passed" if report["passed"] else "failed",
        "tested_at": report["tested_at"], "report": "test_report.json",
    }
    _write_text_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    report["report_path"] = str(report_path)
    return report


def run_proposal_tests_cli(project_root: str | Path, result_path: str | Path) -> int:
    """Entry point used by the frozen EXE to run tests without an external Python install."""
    import contextlib
    import io
    import os
    import unittest
    root = Path(project_root).expanduser().resolve()
    output_path = Path(result_path).expanduser().resolve()
    result_data: dict[str, Any] = {"passed": False, "stdout": "", "stderr": "", "tests_run": 0}
    stream = io.StringIO()
    old_cwd = Path.cwd()
    try:
        os.chdir(root)
        sys.path.insert(0, str(root))
        loader = unittest.TestLoader()
        suite = loader.discover(str(root / "tests"), pattern="test_*.py", top_level_dir=str(root))
        runner = unittest.TextTestRunner(stream=stream, verbosity=2)
        result = runner.run(suite)
        result_data.update(
            passed=result.wasSuccessful(), tests_run=result.testsRun,
            failures=len(result.failures), errors=len(result.errors), stdout=stream.getvalue(),
        )
    except Exception as exc:
        result_data["stderr"] = str(exc)
    finally:
        try:
            os.chdir(old_cwd)
        except OSError:
            pass
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return 2
    return 0 if result_data.get("passed") else 1
