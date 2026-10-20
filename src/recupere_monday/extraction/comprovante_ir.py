from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import fitz

from recupere_monday.extraction.numero_meses_rra import (
    MESES_BB_PATTERN,
    MESES_GENERIC_PATTERN,
    extract_numero_meses_from_text,
    normalize_numero_meses_rra,
)


@dataclass(frozen=True)
class ComprovanteIRData:
    valor_ir_retido: str | None
    tem_ir_retido: bool
    campo_origem: str | None = None
    deducao_previdenciaria: str | None = None
    numero_meses_rra: str | None = None
    requerente_nome: str | None = None
    requerente_cpf_cnpj: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_ir_from_comprovante(pdf_path: Path, banco: str) -> ComprovanteIRData:
    banco_normalizado = str(banco or "").upper()
    if banco_normalizado == "BB":
        primary = _extract_bb_ir(pdf_path)
    elif banco_normalizado == "CEF":
        primary = _extract_caixa_ir(pdf_path)
    else:
        primary = _extract_generic_ir(pdf_path)

    if primary.tem_ir_retido:
        return primary

    # Fallback: demonstrativo pode indicar CEF enquanto o comprovante é BB (ou vice-versa).
    for alt in (_extract_bb_ir, _extract_caixa_ir, _extract_generic_ir):
        try:
            alt_data = alt(pdf_path)
        except Exception:
            continue
        if alt_data.tem_ir_retido:
            return alt_data
    return primary


def _extract_bb_ir(pdf_path: Path) -> ComprovanteIRData:
    text = _extract_text(pdf_path)
    valor_ir = _search_money(
        text,
        [
            r"Valor\s+do\s+IR\s*:\s*R\$\s*([\d.]+,\d{2})",
            r"Imposto\s+Retido\s+na\s+Fonte\s*R\$\s*([\d.]+,\d{2})",
        ],
    )
    return ComprovanteIRData(
        valor_ir_retido=valor_ir,
        tem_ir_retido=_money_to_decimal(valor_ir) > 0,
        campo_origem="Valor do IR" if valor_ir is not None else None,
        deducao_previdenciaria=None,
        numero_meses_rra=extract_numero_meses_from_text(text, MESES_BB_PATTERN),
        requerente_nome=_search_text(text, r"Benefici[aá]rio\s*:\s*(.+)"),
        requerente_cpf_cnpj=_search_text(text, r"CPF/CNPJ\s*:\s*([\d./-]+)"),
    )


def _extract_caixa_ir(pdf_path: Path) -> ComprovanteIRData:
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count == 0:
            return ComprovanteIRData(valor_ir_retido=None, tem_ir_retido=False)

        page = doc[0]
        words = page.get_text("words")
        valor_ir = _extract_money_near_label(
            words,
            required_terms=("Imposto", "Retido", "Fonte"),
        )
        deducao_previdenciaria = _extract_money_near_label(
            words,
            required_terms=("Dedu", "Previd"),
        )
        numero_meses_rra = _extract_number_near_label(
            words,
            required_terms=("Meses", "RRA"),
        )
        nome = _extract_value_on_label_row(words, required_terms=("Nome", "Contribuinte"))
        cpf_cnpj = _extract_value_on_label_row(words, required_terms=("CPF/CNPJ:",))

        return ComprovanteIRData(
            valor_ir_retido=valor_ir,
            tem_ir_retido=_money_to_decimal(valor_ir) > 0,
            campo_origem="Imposto Retido na Fonte" if valor_ir is not None else None,
            deducao_previdenciaria=deducao_previdenciaria,
            numero_meses_rra=numero_meses_rra,
            requerente_nome=nome,
            requerente_cpf_cnpj=cpf_cnpj,
        )
    finally:
        doc.close()


def _extract_generic_ir(pdf_path: Path) -> ComprovanteIRData:
    text = _extract_text(pdf_path)
    valor_ir = _search_money(
        text,
        [
            r"Valor\s+do\s+IR\s*:\s*R\$\s*([\d.]+,\d{2})",
            r"Imposto\s+Retido\s+na\s+Fonte\s*R\$\s*([\d.]+,\d{2})",
        ],
    )
    return ComprovanteIRData(
        valor_ir_retido=valor_ir,
        tem_ir_retido=_money_to_decimal(valor_ir) > 0,
        campo_origem="valor_ir_retido" if valor_ir is not None else None,
        deducao_previdenciaria=_search_money(
            text,
            [
                r"Dedu[cç][aã]o\s+Previdenci[aá]ria\s*R\$\s*([\d.]+,\d{2})",
                r"Dedu[cç][aã]o\s+Previd[eê]ncia\s+Oficial\s*R\$\s*([\d.]+,\d{2})",
            ],
        ),
        numero_meses_rra=extract_numero_meses_from_text(text, MESES_GENERIC_PATTERN),
    )


def _extract_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _search_money(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _normalize_money(match.group(1))
    return None


def _search_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _extract_money_near_label(
    words: list[tuple[Any, ...]],
    *,
    required_terms: tuple[str, ...],
) -> str | None:
    label_y = _find_label_y(words, required_terms)
    if label_y is None:
        return None

    money_tokens = _currency_tokens(words)
    same_row = sorted(
        (token for token in money_tokens if abs(token["y"] - label_y) <= 8),
        key=lambda token: token["x"],
    )
    if not same_row:
        return None
    return _normalize_money(str(same_row[0]["value"]))


def _extract_value_on_label_row(
    words: list[tuple[Any, ...]],
    *,
    required_terms: tuple[str, ...],
) -> str | None:
    label_y = _find_label_y(words, required_terms)
    if label_y is None:
        return None

    row_words = [
        str(word[4])
        for word in sorted(words, key=lambda item: (item[1], item[0]))
        if word[0] >= 250 and abs(float(word[1]) - label_y) <= 8
    ]
    if not row_words:
        return None
    return re.sub(r"\s+", " ", " ".join(row_words)).strip()


def _extract_number_near_label(
    words: list[tuple[Any, ...]],
    *,
    required_terms: tuple[str, ...],
) -> str | None:
    label_y = _find_label_y(words, required_terms)
    if label_y is None:
        return None

    candidates = [
        str(word[4])
        for word in sorted(words, key=lambda item: item[0])
        if word[0] >= 250
        and abs(float(word[1]) - label_y) <= 8
        and re.fullmatch(r"\d+", str(word[4]))
    ]
    if not candidates:
        return None
    if len(candidates) >= 2 and all(len(token) == 1 for token in candidates[:2]):
        return normalize_numero_meses_rra("".join(candidates[:2]))
    return normalize_numero_meses_rra(candidates[0])


def _find_label_y(words: list[tuple[Any, ...]], required_terms: tuple[str, ...]) -> float | None:
    rows: dict[int, list[str]] = {}
    row_y: dict[int, float] = {}
    for word in words:
        y_bucket = round(float(word[1]) / 4)
        rows.setdefault(y_bucket, []).append(str(word[4]).lower())
        row_y.setdefault(y_bucket, float(word[1]))

    terms = tuple(term.lower() for term in required_terms)
    for bucket, row_words in rows.items():
        row_text = " ".join(row_words)
        if all(term in row_text for term in terms):
            return row_y[bucket]
    return None


def _currency_tokens(words: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    sorted_words = sorted(words, key=lambda item: (item[5], item[6], item[7]))
    for index, word in enumerate(sorted_words):
        text = str(word[4])
        if text == "R$" and index + 1 < len(sorted_words):
            next_word = sorted_words[index + 1]
            next_text = str(next_word[4])
            if re.fullmatch(r"[\d.]+,\d{2}", next_text):
                tokens.append(
                    {
                        "value": next_text,
                        "x": float(word[0]),
                        "y": float(word[1]),
                    }
                )
        elif re.fullmatch(r"R\$\s*[\d.]+,\d{2}", text):
            tokens.append(
                {
                    "value": text.replace("R$", "").strip(),
                    "x": float(word[0]),
                    "y": float(word[1]),
                }
            )
    return tokens


def _normalize_money(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"([\d.]+,\d{2})", value)
    if not match:
        return None
    return match.group(1)


def _money_to_decimal(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")
    normalized = value.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return Decimal("0")
