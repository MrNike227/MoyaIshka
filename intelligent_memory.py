# -*- coding: utf-8 -*-
"""Private, local-first intelligent memory for ISH.

Legacy memory remains a list of strings. Metadata is kept in a parallel list,
so existing settings, backup archives and cloud snapshots stay compatible.
Optional Ollama embeddings are used only through a loopback address; the
deterministic lexical fallback works without Ollama or additional packages.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

MAX_MEMORIES = 2000
MAX_MEMORY_TEXT = 500
MAX_EMBEDDING_ITEMS = 120
MAX_CACHE_BYTES = 5 * 1024 * 1024
MEMORY_CONTEXT_START = "[[MOYA_MEMORY_CONTEXT_START]]"
MEMORY_CONTEXT_END = "[[MOYA_MEMORY_CONTEXT_END]]"

_BLOCKED = re.compile(
    r"парол|password|api[_ -]?key|токен|token|секрет|cvv|cvc|банковск|номер карт|паспорт|"
    r"медицин|диагноз|болезн|лекарств|религи|вероисповед|политическ|ориентац|"
    r"социальн\w* номер|личн\w* код|одноразов\w* код|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?:\+?\d[\d\s().-]{7,}\d)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-zа-яё0-9_]{2,}", re.IGNORECASE)
_STOPWORDS = {
    "это", "как", "что", "для", "или", "при", "над", "под", "про", "мне",
    "мой", "моя", "мои", "мое", "его", "её", "они", "оно", "она", "есть",
    "был", "быть", "будет", "уже", "ещё", "так", "там", "тут", "у", "меня",
    "the", "and", "for", "with", "from", "that", "this", "you", "your",
}
_SUFFIXES = (
    "иями", "ями", "ами", "ого", "ему", "ыми", "ими", "ать", "ять",
    "ого", "его", "ому", "ей", "ам", "ям", "ах", "ях", "ой", "ый",
    "ая", "ое", "ые", "ий", "ым", "им", "ом", "ем", "а", "я", "ы",
    "и", "у", "ю", "е", "о", "s", "es", "ed", "ing",
)
_SYNONYM_GROUPS = (
    ("коротк", "кратк", "лаконич", "сжато", "кратко", "короче"),
    ("подробн", "детальн", "развернут", "расширен", "полный"),
    ("компьютер", "ноутбук", "пк", "рабочая", "станция", "устройство"),
    ("ошибк", "сбо", "неисправност", "проблем"),
    ("разработк", "код", "программирован", "приложен", "проект"),
    ("цель", "задач", "план", "намерен"),
)
_SYNONYM_MAP: dict[str, set[str]] = {}
for _group in _SYNONYM_GROUPS:
    _expanded = set(_group)
    for _word in _group:
        _SYNONYM_MAP[_word] = _expanded


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_MEMORY_TEXT]


def contains_sensitive_data(value: object) -> bool:
    return bool(_BLOCKED.search(str(value or "")))


def _tokens(value: str) -> list[str]:
    result = []
    for token in _TOKEN_RE.findall(value or ""):
        token = token.casefold()
        if token in _STOPWORDS:
            continue
        for suffix in _SUFFIXES:
            if len(token) - len(suffix) >= 4 and token.endswith(suffix):
                token = token[:-len(suffix)]
                break
        result.append(token)
    return result


def _expanded_tokens(value: str) -> set[str]:
    expanded: set[str] = set()
    for token in _tokens(value):
        expanded.add(token)
        for key, group in _SYNONYM_MAP.items():
            if token.startswith(key) or key.startswith(token):
                expanded.update(group)
    return expanded


def _category(text: str) -> str:
    value = text.casefold()
    if re.search(r"\b(сегодня|завтра|временно|на этой неделе|до конца недели|в ближайшие дни|пока что)\b|сейчас (?:работаю|занимаюсь|делаю|готовлю)", value):
        return "Временные задачи"
    if re.search(r"предпочита|нравится|люблю|не люблю|мне важно|мой стиль", value):
        return "Предпочтения"
    if re.search(r"проект|приложени|репозитор|разработ|код", value):
        return "Проекты"
    if re.search(r"цель|планирую|моя задача|хочу добиться", value):
        return "Цели и планы"
    if re.search(r"windows|linux|macos|python|ollama|компьютер|ноутбук|процессор|видеокарт|ssd|озу|github|supabase", value):
        return "Техника и инструменты"
    return "Факты"


def _importance(text: str) -> int:
    value = text.casefold()
    if re.search(r"всегда|никогда|постоянно|очень важно|обязательно|главная цель|основная цель", value):
        return 5
    if re.search(r"запомни|предпочита|нравится|мой проект|моя цель|мне важно", value):
        return 4
    return 3


def _slot_key(text: str) -> str:
    value = text.casefold()
    if re.search(r"(?:коротк|кратк|лаконич|подробн|детальн|развернут)", value) and re.search(r"ответ|отвеч", value):
        return "preference:answer_style"
    if re.search(r"(?:меня зовут|моё имя|мое имя)", value):
        return "profile:name"
    if re.search(r"(?:мой проект называется|проект называется)", value):
        return "project:name"
    if re.search(r"(?:моя главная цель|моя основная цель)", value):
        return "goal:primary"
    return ""


def _expiry(text: str, now: datetime | None = None) -> str:
    value = text.casefold()
    base = now or _now()
    if "сегодня" in value:
        return _iso(base + timedelta(days=1))
    if "завтра" in value:
        return _iso(base + timedelta(days=2))
    if re.search(r"на этой неделе|до конца недели|в ближайшие дни", value):
        return _iso(base + timedelta(days=7))
    if re.search(r"временно|пока что|сейчас (?:работаю|занимаюсь|делаю|готовлю)", value):
        return _iso(base + timedelta(days=30))
    return ""


def _new_metadata(text: str, *, source: str = "legacy", now: datetime | None = None) -> dict[str, Any]:
    stamp = _iso(now or _now())
    return {
        "id": uuid.uuid4().hex,
        "category": _category(text),
        "importance": _importance(text),
        "created_at": stamp,
        "updated_at": stamp,
        "expires_at": _expiry(text, now),
        "status": "active",
        "source": str(source)[:20],
        "slot_key": _slot_key(text),
        "superseded_by": "",
    }


def normalize_memory_metadata(
    memories: Iterable[object],
    metadata: Iterable[object] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Migrate legacy string memories to safe metadata without changing their text."""
    raw_memories = list(memories) if isinstance(memories, (list, tuple)) else []
    raw_metadata = list(metadata) if isinstance(metadata, (list, tuple)) else []
    clean_memories: list[str] = []
    clean_metadata: list[dict[str, Any]] = []
    now = _now()
    for index, value in enumerate(raw_memories[:MAX_MEMORIES]):
        if not isinstance(value, str):
            continue
        text = normalize_text(value)
        if not text:
            continue
        old = raw_metadata[index] if index < len(raw_metadata) and isinstance(raw_metadata[index], dict) else {}
        inferred = _new_metadata(text, source="legacy", now=now)
        record = dict(inferred)
        for key in ("id", "category", "created_at", "updated_at", "expires_at", "source", "slot_key", "superseded_by"):
            if isinstance(old.get(key), str) and old.get(key):
                record[key] = old[key][:100] if key in {"id", "source", "slot_key", "superseded_by"} else old[key][:64] if key in {"created_at", "updated_at", "expires_at"} else old[key][:60]
        try:
            record["importance"] = max(1, min(5, int(old.get("importance", inferred["importance"]))))
        except (TypeError, ValueError):
            record["importance"] = inferred["importance"]
        status = str(old.get("status", "active"))
        record["status"] = status if status in {"active", "superseded", "expired"} else "active"
        expiry = _parse_time(record.get("expires_at", ""))
        if record["status"] == "active" and expiry and expiry <= now:
            record["status"] = "expired"
        clean_memories.append(text)
        clean_metadata.append(record)
    return clean_memories, clean_metadata


def add_or_update_memory(
    memories: Iterable[object],
    metadata: Iterable[object] | None,
    text: str,
    *,
    source: str = "manual",
) -> tuple[list[str], list[dict[str, Any]], str]:
    """Add a fact, merge duplicates, or supersede a high-confidence conflicting slot."""
    clean_text = normalize_text(text)
    current, details = normalize_memory_metadata(memories, metadata)
    if not clean_text:
        return current, details, "empty"
    if contains_sensitive_data(clean_text):
        return current, details, "blocked"
    key = clean_text.casefold()
    for index, existing in enumerate(current):
        if existing.casefold() == key:
            details[index]["updated_at"] = _iso(_now())
            details[index]["importance"] = max(int(details[index].get("importance", 3)), _importance(clean_text))
            return current, details, "duplicate"
    slot = _slot_key(clean_text)
    new_record = _new_metadata(clean_text, source=source)
    if slot:
        for record in details:
            if record.get("slot_key") == slot and record.get("status") == "active":
                record["status"] = "superseded"
                record["superseded_by"] = new_record["id"]
                record["updated_at"] = _iso(_now())
    current.append(clean_text)
    details.append(new_record)
    if len(current) > MAX_MEMORIES:
        current = current[-MAX_MEMORIES:]
        details = details[-MAX_MEMORIES:]
    return current, details, "superseded" if slot and any(item.get("slot_key") == slot and item.get("status") == "superseded" for item in details[:-1]) else "added"


def edit_memory_entry(
    memories: Iterable[object],
    metadata: Iterable[object] | None,
    index: int,
    text: str,
) -> tuple[list[str], list[dict[str, Any]], str]:
    current, details = normalize_memory_metadata(memories, metadata)
    clean_text = normalize_text(text)
    if not 0 <= int(index) < len(current) or not clean_text:
        return current, details, "invalid"
    if contains_sensitive_data(clean_text):
        return current, details, "blocked"
    for other_index, existing in enumerate(current):
        if other_index != index and existing.casefold() == clean_text.casefold():
            return current, details, "duplicate"
    old = details[index]
    slot = _slot_key(clean_text)
    updated = _new_metadata(clean_text, source="edited")
    updated["id"] = old["id"]
    updated["created_at"] = old["created_at"]
    updated["updated_at"] = _iso(_now())
    if slot:
        for other_index, record in enumerate(details):
            if other_index != index and record.get("slot_key") == slot and record.get("status") == "active":
                record["status"] = "superseded"
                record["superseded_by"] = updated["id"]
                record["updated_at"] = _iso(_now())
    details[index] = updated
    current[index] = clean_text
    return current, details, "updated"


def merge_memory_data(
    local_memories: Iterable[object],
    local_metadata: Iterable[object] | None,
    remote_memories: Iterable[object],
    remote_metadata: Iterable[object] | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Merge local/cloud memories by content and keep the newest metadata."""
    local_texts, local_details = normalize_memory_metadata(local_memories, local_metadata)
    remote_texts, remote_details = normalize_memory_metadata(remote_memories, remote_metadata)
    merged: dict[str, tuple[str, dict[str, Any]]] = {}
    for texts, details in ((local_texts, local_details), (remote_texts, remote_details)):
        for text, record in zip(texts, details):
            key = text.casefold()
            previous = merged.get(key)
            if previous is None:
                merged[key] = (text, dict(record))
                continue
            old_time = _parse_time(previous[1].get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)
            new_time = _parse_time(record.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)
            if new_time >= old_time:
                merged[key] = (text, dict(record))
    ordered = sorted(merged.values(), key=lambda item: str(item[1].get("created_at", "")))[-MAX_MEMORIES:]
    # Resolve duplicate active values only for explicit, high-confidence slots.
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for _, record in ordered:
        slot = str(record.get("slot_key", ""))
        if slot and record.get("status") == "active":
            by_slot.setdefault(slot, []).append(record)
    for records in by_slot.values():
        if len(records) > 1:
            winner = max(records, key=lambda item: str(item.get("updated_at", "")))
            for record in records:
                if record is not winner:
                    record["status"] = "superseded"
                    record["superseded_by"] = str(winner.get("id", ""))
    return [item[0] for item in ordered], [item[1] for item in ordered]


def _lexical_score(text: str, query: str) -> float:
    query_tokens = _expanded_tokens(query)
    if not query_tokens:
        return 0.0
    memory_tokens = _expanded_tokens(text)
    overlap = query_tokens.intersection(memory_tokens)
    if not overlap:
        score = 0.0
    else:
        score = 0.55 * len(overlap) / max(1, len(query_tokens))
        score += 0.45 * len(overlap) / max(1, len(memory_tokens))
    normalized_query = re.sub(r"\s+", " ", query.casefold()).strip()
    if len(normalized_query) >= 5 and normalized_query in text.casefold():
        score = max(score, 1.0)
    return min(1.0, score)


def _cache_key(text: str) -> str:
    return hashlib.sha256(normalize_text(text).casefold().encode("utf-8")).hexdigest()


def _load_cache(path: Path | None, model: str) -> dict[str, list[float]]:
    if path is None:
        return {}
    try:
        if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("model") != model or not isinstance(data.get("vectors"), dict):
            return {}
        result: dict[str, list[float]] = {}
        for key, vector in list(data["vectors"].items())[-MAX_EMBEDDING_ITEMS:]:
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
                continue
            if not isinstance(vector, list) or not 1 <= len(vector) <= 4096:
                continue
            values = [float(item) for item in vector]
            if all(math.isfinite(item) for item in values):
                result[key] = values
        return result
    except (OSError, ValueError, TypeError):
        return {}


def _save_cache(path: Path | None, model: str, vectors: dict[str, list[float]]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        limited = dict(list(vectors.items())[-MAX_EMBEDDING_ITEMS:])
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"model": model, "vectors": limited}, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def _embed_texts(ollama_url: str, model: str, texts: list[str], timeout: float = 10.0) -> list[list[float]] | None:
    """Get vectors only from a local Ollama server; memory never goes to a remote host."""
    try:
        parsed = urllib.parse.urlsplit(str(ollama_url or "").strip().rstrip("/"))
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in {"localhost", "127.0.0.1", "::1"}:
            return None
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        if parsed.port not in {None, 80, 443, 11434}:
            return None
        base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        url = base + "/api/embed"
        payload = json.dumps({"model": model, "input": texts, "keep_alive": "0"}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=max(1.0, min(float(timeout), 12.0))) as response:
            data = json.loads(response.read(8 * 1024 * 1024 + 1).decode("utf-8"))
        embeddings = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            return None
        result: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not 1 <= len(vector) <= 4096:
                return None
            values = [float(item) for item in vector]
            if not all(math.isfinite(item) for item in values):
                return None
            result.append(values)
        return result
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        return None


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def select_memory_records(
    memories: Iterable[object],
    metadata: Iterable[object] | None,
    query: str,
    *,
    max_items: int = 8,
    max_chars: int = 2800,
    semantic_enabled: bool = True,
    ollama_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    cache_path: str | Path | None = None,
    resource_pressure: bool = False,
) -> list[dict[str, Any]]:
    """Rank current memories using local embeddings + lexical relevance + importance."""
    if max_items <= 0 or max_chars <= 0:
        return []
    texts, details = normalize_memory_metadata(memories, metadata)
    now = _now()
    active: list[tuple[str, dict[str, Any], float]] = []
    for text, record in zip(texts, details):
        if record.get("status") != "active" or contains_sensitive_data(text):
            continue
        expiry = _parse_time(record.get("expires_at"))
        if expiry and expiry <= now:
            continue
        lexical = _lexical_score(text, query)
        active.append((text, record, lexical))
    if not active:
        return []

    vectors: dict[str, list[float]] = {}
    query_vector: list[float] | None = None
    model = str(embedding_model or "").strip()[:120]
    cache_file = Path(cache_path).expanduser() if cache_path else None
    can_embed = bool(semantic_enabled and not resource_pressure and model and query.strip() and ollama_url)
    if can_embed:
        vectors = _load_cache(cache_file, model)
        query_vector_hash = _cache_key("query:" + query)
        pending: list[str] = [query]
        pending_keys: list[str] = [query_vector_hash]
        candidate_texts = sorted(active, key=lambda item: (item[2], int(item[1].get("importance", 3))), reverse=True)[:MAX_EMBEDDING_ITEMS]
        for text, _, _ in candidate_texts:
            key = _cache_key(text)
            if key not in vectors:
                pending.append(text)
                pending_keys.append(key)
        generated = _embed_texts(str(ollama_url), model, pending)
        if generated and len(generated) == len(pending):
            query_vector = generated[0]
            for key, vector in zip(pending_keys[1:], generated[1:]):
                vectors[key] = vector
            _save_cache(cache_file, model, vectors)
        else:
            query_vector = None
        if query_vector is None and query_vector_hash in vectors:
            query_vector = vectors.get(query_vector_hash)

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for text, record, lexical in active:
        semantic = 0.0
        if query_vector is not None:
            vector = vectors.get(_cache_key(text))
            if vector:
                semantic = max(0.0, _cosine(query_vector, vector))
        importance = max(1, min(5, int(record.get("importance", 3))))
        importance_score = (importance - 1) / 4
        created = _parse_time(record.get("updated_at")) or _parse_time(record.get("created_at")) or now
        age_days = max(0.0, (now - created).total_seconds() / 86400)
        recency_score = math.exp(-age_days / 120.0)
        if query_vector is not None:
            score = 0.68 * semantic + 0.24 * lexical + 0.05 * importance_score + 0.03 * recency_score
            relevant = semantic >= 0.22 or lexical > 0
        else:
            score = 0.82 * lexical + 0.12 * importance_score + 0.06 * recency_score
            relevant = lexical > 0
        if relevant or not query.strip():
            ranked.append((score, text, record))
    if not ranked:
        # Keep a small, useful fallback rather than injecting the entire memory.
        ranked = [
            (0.12 * max(1, min(5, int(record.get("importance", 3)))) / 5 + 0.06 * math.exp(
                -max(0.0, (now - (_parse_time(record.get("updated_at")) or now)).total_seconds()) / (120 * 86400)
            ), text, record)
            for text, record, _ in active
        ]
    ranked.sort(key=lambda item: item[0], reverse=True)

    result: list[dict[str, Any]] = []
    used = 0
    for _, text, record in ranked:
        available = max_chars - used
        if available <= 0:
            break
        value = text
        if len(value) > available:
            if available < 80:
                continue
            value = value[:available - 1].rstrip() + "…"
        result.append({
            "text": value,
            "category": str(record.get("category", "Факты")),
            "importance": int(record.get("importance", 3)),
            "id": str(record.get("id", "")),
            "source": str(record.get("source", "")),
        })
        used += len(value) + 1
        if len(result) >= max_items:
            break
    return result
