# -*- coding: utf-8 -*-
"""Create review-only GitHub Draft PRs from self-improvement patches.

This module deliberately has no release, tag, merge, or direct-to-default-branch
operation. A proposal is first rebased against the selected GitHub base file.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable

from self_improvement import apply_unified_diff

API_ROOT = "https://api.github.com"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


class GitHubProposalError(RuntimeError):
    pass


def _api_request(
    token: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 25.0,
) -> dict[str, Any]:
    if not token or len(token) > 500:
        raise GitHubProposalError("Добавь GitHub Personal Access Token в настройках саморазвития.")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        API_ROOT + endpoint,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MoyaIshka/4.2.1",
            "Content-Type": "application/json",
        },
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=max(3.0, min(float(timeout), 60.0))) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise GitHubProposalError("Ответ GitHub превышает допустимый размер.")
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise GitHubProposalError("GitHub вернул ответ неожиданного формата.")
        return value
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read(20000).decode("utf-8", errors="replace")
            detail = json.loads(error_body).get("message", "")
        except Exception:
            detail = ""
        if exc.code in {401, 403}:
            raise GitHubProposalError("GitHub отклонил токен или у токена недостаточно прав. Проверь Contents: Read and write и Pull requests: Read and write.") from exc
        if exc.code == 404:
            raise GitHubProposalError("GitHub не нашёл репозиторий, базовую ветку или файл. Проверь имя репозитория и права токена.") from exc
        raise GitHubProposalError(f"GitHub API вернул HTTP {exc.code}: {str(detail)[:500]}") from exc
    except urllib.error.URLError as exc:
        raise GitHubProposalError(f"Не удалось подключиться к GitHub: {exc.reason}") from exc
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise GitHubProposalError(f"Не удалось обработать ответ GitHub: {exc}") from exc


def _safe_repo_path(value: str) -> str:
    path = str(value or "").replace("\\", "/").strip()
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
        or path.casefold().startswith((".git/", ".github/workflows/"))
        or not path.lower().endswith(".py")
    ):
        raise GitHubProposalError("Draft PR разрешено создавать только для безопасного относительного пути к Python-файлу.")
    return path


def create_draft_pull_request(
    repository: str,
    token: str,
    base_branch: str,
    file_path: str,
    patch_text: str,
    *,
    title: str = "Предложение улучшения от Моя ИИшка",
    body: str = "",
    request: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebase one file patch onto GitHub base and open a Draft PR (never a release)."""
    repo = str(repository or "").strip().strip("/")
    if not _REPO_RE.fullmatch(repo):
        raise GitHubProposalError("Укажи GitHub-репозиторий в формате owner/repository.")
    if not token:
        raise GitHubProposalError("Для создания Draft PR нужен GitHub Personal Access Token.")
    base = str(base_branch or "").strip()
    if base and (not _BRANCH_RE.fullmatch(base) or base.startswith("/") or ".." in base.split("/")):
        raise GitHubProposalError("Указана небезопасная базовая ветка.")
    path = _safe_repo_path(file_path)
    patch = str(patch_text or "").strip()
    if not patch or len(patch.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise GitHubProposalError("Diff пустой или превышает 2 МБ.")
    api = request or _api_request
    prefix = "/repos/" + repo

    repo_info = api(token, "GET", prefix)
    if not base:
        base = str(repo_info.get("default_branch") or "main")
    ref = api(token, "GET", prefix + "/git/ref/heads/" + urllib.parse.quote(base, safe="/"))
    base_sha = str(ref.get("object", {}).get("sha", ""))
    if not base_sha:
        raise GitHubProposalError("Не удалось определить последний коммит базовой ветки.")
    base_commit = api(token, "GET", prefix + "/git/commits/" + base_sha)
    base_tree = str(base_commit.get("tree", {}).get("sha", ""))
    if not base_tree:
        raise GitHubProposalError("GitHub не вернул дерево базового коммита.")

    content_endpoint = prefix + "/contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + urllib.parse.quote(base, safe="")
    content_result = api(token, "GET", content_endpoint)
    if str(content_result.get("encoding", "")).lower() != "base64":
        raise GitHubProposalError("GitHub вернул файл в неподдерживаемой кодировке.")
    try:
        original_bytes = base64.b64decode("".join(str(content_result.get("content", "")).split()), validate=True)
        if len(original_bytes) > MAX_SOURCE_BYTES:
            raise GitHubProposalError("Исходный файл на GitHub превышает лимит 2 МБ.")
        original = original_bytes.decode("utf-8")
        updated = apply_unified_diff(original, patch, path)
        compile(updated, path, "exec")
    except GitHubProposalError:
        raise
    except Exception as exc:
        raise GitHubProposalError(
            "Diff не удалось безопасно применить к текущей версии файла на GitHub. "
            "Сначала обнови локальные исходники и подготовь diff заново."
        ) from exc
    if updated == original:
        raise GitHubProposalError("Diff не изменяет файл; пустой PR не создан.")

    blob = api(token, "POST", prefix + "/git/blobs", {"content": updated, "encoding": "utf-8"})
    blob_sha = str(blob.get("sha", ""))
    if not blob_sha:
        raise GitHubProposalError("GitHub не создал blob с изменённым файлом.")
    tree = api(token, "POST", prefix + "/git/trees", {
        "base_tree": base_tree,
        "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
    })
    tree_sha = str(tree.get("sha", ""))
    if not tree_sha:
        raise GitHubProposalError("GitHub не создал дерево изменений.")
    timestamp = uuid.uuid4().hex[:10]
    branch = f"moya-improvement/{timestamp}"
    commit = api(token, "POST", prefix + "/git/commits", {
        "message": str(title).strip()[:120] or "Propose code improvement",
        "tree": tree_sha,
        "parents": [base_sha],
    })
    commit_sha = str(commit.get("sha", ""))
    if not commit_sha:
        raise GitHubProposalError("GitHub не создал коммит для предложения.")
    api(token, "POST", prefix + "/git/refs", {
        "ref": "refs/heads/" + branch,
        "sha": commit_sha,
    })
    pr_body = (
        "Это Draft PR, автоматически подготовленный модулем саморазвития Моя ИИшка.\n\n"
        "Изменения рассчитаны относительно актуального файла базовой ветки. "
        "Проверь код и запусти CI перед слиянием.\n\n"
        + str(body).strip()[:4000]
    )
    try:
        pr = api(token, "POST", prefix + "/pulls", {
            "title": str(title).strip()[:200] or "Предложение улучшения от Моя ИИшка",
            "head": branch,
            "base": base,
            "body": pr_body,
            "draft": True,
        })
    except Exception:
        try:
            api(token, "DELETE", prefix + "/git/refs/heads/" + urllib.parse.quote(branch, safe="/"))
        except Exception:
            pass
        raise
    if not pr.get("html_url") or not pr.get("number"):
        raise GitHubProposalError("GitHub не подтвердил создание Draft PR.")
    return {
        "number": int(pr["number"]),
        "url": str(pr["html_url"]),
        "branch": branch,
        "base": base,
        "repository": repo,
        "draft": bool(pr.get("draft", True)),
    }
