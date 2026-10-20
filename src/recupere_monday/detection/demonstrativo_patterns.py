from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# Marcadores da página 1 (cabeçalho TRF4) — derivados dos modelos em modelos/demonstrativos/
PAGE1_REQUIRED_PHRASE = "TRF4 - DEMONSTRATIVO DE PAGAMENTO"
PAGE1_SUPPORT_PHRASES = (
    "PROCESSO",
    "ORIGINARIO",
    "REQUERENTE",
    "REQUISICAO",
)

# Marcadores da página 2 (tabela de valores) — presença confirma par completo
PAGE2_REQUIRED_PHRASE = "VALORES DA REQUISICAO DO JUIZO DE ORIGEM"
PAGE2_SUPPORT_PHRASES = (
    "DATA BASE DO CALCULO",
    "ATUALIZACAO MONETARIA",
    "PRINCIPAL CORRIGIDO",
    "JUROS DE MORA",
)


@dataclass(frozen=True)
class DemonstrativoMatch:
    source_pdf: str
    process_folder: str
    start_page: int  # 1-based
    end_page: int  # 1-based
    requisicao_processo: str | None = None
    output_path: str | None = None


@dataclass
class DetectionResult:
    scanned_pdfs: int = 0
    matches_found: int = 0
    extracted: int = 0
    failed: int = 0
    matches: list[DemonstrativoMatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).upper().strip()


def score_page1(text: str) -> int:
    norm = normalize_text(text)
    if PAGE1_REQUIRED_PHRASE not in norm:
        return 0
    score = 10
    for phrase in PAGE1_SUPPORT_PHRASES:
        if phrase in norm:
            score += 2
    return score


def score_page2(text: str) -> int:
    norm = normalize_text(text)
    if PAGE2_REQUIRED_PHRASE not in norm:
        return 0
    score = 6
    for phrase in PAGE2_SUPPORT_PHRASES:
        if phrase in norm:
            score += 3
    return score


def extract_process_number(text: str) -> str | None:
    norm = normalize_text(text)
    match = re.search(
        r"PROCESSO\s*:?\s*(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})",
        norm,
    )
    return match.group(1) if match else None


def is_valid_page1(text: str, min_score: int = 14) -> bool:
    return score_page1(text) >= min_score


def is_valid_page2(text: str, min_score: int = 6) -> bool:
    return score_page2(text) >= min_score


def has_bank_footer(text: str) -> bool:
    norm = normalize_text(text)
    return bool(
        re.search(
            r"BANCO\s*:?.+AGENCIA\s*:?.+CONTA\s*DEPOSITO\s*:",
            norm,
        )
    )


def is_valid_creditor_page(text: str) -> bool:
    """Página de um credor: cabeçalho TRF4 completo ou bloco de valores + conta no rodapé."""
    if is_valid_page1(text):
        return True
    return is_valid_page2(text) and has_bank_footer(text)
