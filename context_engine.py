# -*- coding: utf-8 -*-
"""Deterministic relevance selection for personal memory and bounded model context."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

_TOKEN_RE = re.compile(r"[a-zа-яё0-9_]{2,}", re.IGNORECASE)
_STOPWORDS = {
    "это", "как", "что", "для", "или", "при", "над", "под", "про", "мне",
    "мой", "моя", "мои", "мое", "его", "её", "они", "оно", "она", "есть",
    "был", "быть", "будет", "уже", "ещё", "так", "там", "тут", "the",
    "and", "for", "with", "from", "that", "this", "you", "your",
}
_SUFFIXES = (
    "иями", "ями", "ами", "ого", "ему", "ыми", "ими", "ать", "ять",
    "ого", "его", "ому", "ей", "ам", "ям", "ах", "ях", "ой", "ый",
    "ая", "ое", "ые", "ий", "ым", "им", "ом", "ем", "а", "я", "ы",
    "и", "у", "ю", "е", "о", "s", "es", "ed", "ing",
)


def _stem(token: str) -> str:
    word = token.casefold()
    for suffix in _SUFFIXES:
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def _tokens(value: str) -> list[str]:
    return [
        _stem(word) for word in _TOKEN_RE.findall(value or "")
        if word.casefold() not in _STOPWORDS
    ]


def select_relevant_memories(
    memories: Iterable[object],
    query: str,
    *,
    max_items: int = 10,
    max_chars: int = 3600,
) -> list[str]:
    """Rank unique memories by query overlap; recency breaks ties."""
    if max_items <= 0 or max_chars <= 0:
        return []
    clean: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, value in enumerate(memories):
        if not isinstance(value, str):
            continue
        item = re.sub(r"\s+", " ", value).strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            clean.append((index, item))
    if not clean:
        return []

    query_tokens = Counter(_tokens(query))
    if not query_tokens:
        ranked = [(0.0, idx, item) for idx, item in clean[-max_items:]]
    else:
        query_terms = set(query_tokens)
        ranked: list[tuple[float, int, str]] = []
        for index, item in clean:
            item_tokens = Counter(_tokens(item))
            overlap = query_terms.intersection(item_tokens)
            coverage = sum(min(query_tokens[token], item_tokens[token]) for token in overlap)
            score = coverage / max(1, sum(query_tokens.values()))
            if overlap:
                score += min(1.0, len(overlap) / max(1, len(query_terms))) * 0.6
            if query.strip() and query.casefold() in item.casefold():
                score += 1.5
            if score > 0:
                ranked.append((score, index, item))
        if not ranked:
            ranked = [(0.0, idx, item) for idx, item in clean[-min(2, max_items):]]
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        ranked = ranked[:max_items]

    ranked.sort(key=lambda row: row[1])
    selected: list[str] = []
    used = 0
    for _, _, item in ranked:
        available = max_chars - used
        if available <= 0:
            break
        if len(item) > available:
            if available < 80:
                continue
            item = item[:available - 1].rstrip() + "…"
        selected.append(item)
        used += len(item) + 1
    return selected


def trim_conversation_context(
    messages: list[dict[str, str]],
    *,
    max_chars: int = 48_000,
    max_messages: int = 33,
) -> list[dict[str, str]]:
    """Keep the system prompt and newest complete messages within a hard budget."""
    if not messages or max_chars <= 0 or max_messages <= 0:
        return []
    has_system = messages[0].get("role") == "system"
    system = dict(messages[0]) if has_system else None
    conversation = messages[1:] if has_system else list(messages)
    limit = max(0, max_messages - (1 if has_system else 0))
    conversation = conversation[-limit:] if limit else []
    budget = max(0, max_chars - (len(system.get("content", "")) if system else 0))
    kept: list[dict[str, str]] = []
    for message in reversed(conversation):
        content = str(message.get("content", ""))
        if not content:
            continue
        if len(content) <= budget:
            kept.append(dict(message))
            budget -= len(content)
            continue
        if not kept and message.get("role") == "user" and budget >= 800:
            prefix = "[Начало сообщения сокращено для экономии контекста]\n"
            kept.append({"role": "user", "content": prefix + content[-max(0, budget - len(prefix)):]})
        break
    kept.reverse()
    return ([system] if system else []) + kept
