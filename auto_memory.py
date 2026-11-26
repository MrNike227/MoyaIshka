# -*- coding: utf-8 -*-
"""Conservative, rule-based extraction of explicit personal memory."""
from __future__ import annotations

import re

# Never persist likely secrets or highly sensitive personal categories from chat.
_BLOCKED = re.compile(
    r"парол|password|api[_ -]?key|токен|token|секрет|cvv|cvc|банковск|номер карт|паспорт|"
    r"медицин|диагноз|болезн|лекарств|религи|вероисповед|политическ|ориентац|"
    r"социальн\w* номер|личн\w* код|одноразов\w* код|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?:\+?\d[\d\s().-]{7,}\d)",
    re.IGNORECASE,
)
_PATTERNS = [
    re.compile(r"^\s*запомни\s*[:,-]?\s*(.{5,240})$", re.IGNORECASE),
    re.compile(r"^\s*меня зовут\s+(.{2,80})$", re.IGNORECASE),
    re.compile(r"^\s*мо[её]\s+имя\s+(.{2,80})$", re.IGNORECASE),
    re.compile(r"^\s*я предпочитаю\s+(.{3,180})$", re.IGNORECASE),
    re.compile(r"^\s*мне нравится\s+(.{3,180})$", re.IGNORECASE),
    re.compile(r"^\s*мне важно\s+(.{3,180})$", re.IGNORECASE),
    re.compile(r"^\s*мой проект называется\s+(.{2,120})$", re.IGNORECASE),
    re.compile(r"^\s*проект называется\s+(.{2,120})$", re.IGNORECASE),
    re.compile(r"^\s*я работаю с\s+(.{3,120})$", re.IGNORECASE),
    re.compile(r"^\s*моя цель\s+(.{3,180})$", re.IGNORECASE),
    re.compile(r"^\s*моя главная цель\s+(.{3,180})$", re.IGNORECASE),
]


def extract_memory_candidate(text: str) -> str | None:
    """Remember only concise, explicit facts; never infer sensitive details."""
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned or len(cleaned) > 300 or _BLOCKED.search(cleaned):
        return None
    for pattern in _PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue
        value = match.group(1).strip(" .,!\t")
        if not value or _BLOCKED.search(value):
            return None
        # Keep explicit "remember" text verbatim; preserve the statement for context otherwise.
        if pattern.pattern.startswith(r"^\s*запомни"):
            return value[:240]
        return f"Пользователь сообщил: {cleaned}"[:300]
    return None
