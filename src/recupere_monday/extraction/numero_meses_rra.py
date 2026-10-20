# -*- coding: utf-8 -*-
"""Normalização do número de meses RRA (OCR de comprovante BB)."""
from __future__ import annotations

import re
from typing import Any

# RRA usa meses inteiros. OCR de comprovante BB costuma partir "58" em "5,8" / "5 8".
MESES_RRA_MIN = 1
MESES_RRA_MAX = 600
MESES_BB_PATTERN = re.compile(
    r"N[uú]mero\s+de\s+meses\s*:\s*(\d+(?:[,.\s]{1,3}\d+)?)",
    flags=re.IGNORECASE,
)
MESES_GENERIC_PATTERN = re.compile(
    r"N[uú]mero\s+de\s+Meses\s+RRA.*?(\d+(?:[,.\s]{1,3}\d+)?)",
    flags=re.IGNORECASE,
)


def extract_numero_meses_from_text(text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(text or "")
    if not match:
        return None
    return normalize_numero_meses_rra(match.group(1))


def _int_meses_rra(value: Any) -> int | None:
    normalized = normalize_numero_meses_rra(value)
    if normalized is None:
        return None
    try:
        return int(normalized)
    except (TypeError, ValueError):
        return None


def normalize_numero_meses_rra(value: Any) -> str | None:
    """Devolve meses RRA como inteiro em texto.

    Comprovantes BB/OCR frequentemente partem 58 em ``5,8``, ``5.8`` ou ``5 8``.
    Neste campo o valor é inteiro; concatenar os dígitos é o certo.
    """
    if value is None or value == "":
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        return None
    groups = re.findall(r"\d+", text)
    if not groups:
        return None
    concatenated = int("".join(groups))
    if MESES_RRA_MIN <= concatenated <= MESES_RRA_MAX:
        return str(concatenated)
    if len(groups) == 1:
        return None
    first = int(groups[0])
    if MESES_RRA_MIN <= first <= MESES_RRA_MAX:
        return str(first)
    return None


def escolher_numero_meses_rra(
    *,
    extraido_local: Any,
    extraido_ia: Any = None,
) -> str | None:
    """Escolhe meses RRA sem deixar OCR truncado (5) ganhar de um 58 da IA."""
    local_n = _int_meses_rra(extraido_local)
    ia_n = _int_meses_rra(extraido_ia)
    if local_n is None:
        return str(ia_n) if ia_n is not None else None
    if ia_n is None or ia_n == local_n:
        return str(local_n)
    if str(ia_n).startswith(str(local_n)) and ia_n > local_n:
        return str(ia_n)
    if str(local_n).startswith(str(ia_n)) and local_n > ia_n:
        return str(local_n)
    return str(local_n)
