from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

from recupere_monday.detection.demonstrativo_patterns import (
    extract_process_number,
    normalize_text,
)

_BANK_FOOTER_PATTERN = re.compile(
    r"BANCO\s*:?\s*(?P<banco>[A-Z]{2,10})\s+"
    r"AGENCIA\s*:?\s*(?P<agencia>\d+)\s+"
    r"CONTA\s*DEPOSITO\s*:?\s*(?P<conta>\d+)"
)


@dataclass(frozen=True)
class DemonstrativoCampos:
    banco: str | None = None
    agencia: str | None = None
    conta_deposito: str | None = None
    processo_requisicao: str | None = None
    beneficiario_nome: str | None = None
    beneficiario_cpf_cnpj: str | None = None
    requerido: str | None = None
    advogado: str | None = None
    mes_pagamento: str | None = None
    data_disponibilidade_saque: str | None = None
    juros_mora_selic: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @property
    def has_dados_bancarios(self) -> bool:
        return bool(self.agencia and self.conta_deposito)


def extract_campos_from_text(page1_text: str) -> DemonstrativoCampos:
    """Extrai agência, conta e demais campos da página do credor."""
    norm = normalize_text(page1_text)
    bank = _extract_bank_footer(norm)
    beneficiary = _extract_beneficiary(page1_text)

    return DemonstrativoCampos(
        banco=bank.get("banco"),
        agencia=bank.get("agencia"),
        conta_deposito=bank.get("conta"),
        processo_requisicao=extract_process_number(page1_text),
        beneficiario_nome=beneficiary.get("nome"),
        beneficiario_cpf_cnpj=beneficiary.get("cpf_cnpj"),
        requerido=_extract_labeled_line(page1_text, "REQUERIDO"),
        advogado=_extract_labeled_line(page1_text, "ADVOGADO"),
        mes_pagamento=_extract_mes_pagamento(page1_text),
        data_disponibilidade_saque=_extract_data_disponibilidade_saque(page1_text),
        juros_mora_selic=_extract_juros_mora_selic(page1_text),
    )


def extract_campos_from_pdf(pdf_path: Path) -> DemonstrativoCampos:
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count == 0:
            return DemonstrativoCampos()
        return extract_campos_from_text(doc[0].get_text())
    finally:
        doc.close()


def _extract_bank_footer(norm_text: str) -> dict[str, str]:
    match = _BANK_FOOTER_PATTERN.search(norm_text)
    if not match:
        return {}
    return {
        "banco": match.group("banco"),
        "agencia": match.group("agencia"),
        "conta": match.group("conta"),
    }


def _extract_beneficiary(raw_text: str) -> dict[str, str]:
    compact_text = re.sub(r"\s+", " ", raw_text)
    compact_match = re.search(
        r"(.+?)\s*-\s*(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/\d{4}-\d{2})\s*\(\s*Benefici[aá]rio\s+quitado",
        compact_text,
        re.IGNORECASE,
    )
    if compact_match:
        nome = compact_match.group(1).split("Imposto de Renda:")[-1].strip()
        if "Federal." in nome:
            nome = nome.split("Federal.")[-1].strip()
        return {"nome": nome, "cpf_cnpj": compact_match.group(2)}

    for line in raw_text.splitlines():
        line = line.strip()
        if not line or "Beneficiário quitado" not in line and "Beneficiario quitado" not in line:
            continue
        if "Beneficiário quitado" in line:
            candidate = line.split("Beneficiário quitado")[0].strip(" (")
        else:
            candidate = line.split("Beneficiario quitado")[0].strip(" (")
        match = re.search(
            r"^(.+?)\s*-\s*(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/\d{4}-\d{2})",
            candidate,
        )
        if match:
            return {"nome": match.group(1).strip(), "cpf_cnpj": match.group(2)}
    return {}


def _extract_labeled_line(raw_text: str, label: str) -> str | None:
    pattern = rf"\b{re.escape(label)}\b\s*:?\s*(.+?)\s*$"
    lines = raw_text.splitlines()
    for index, line in enumerate(lines):
        match = re.search(pattern, line, re.IGNORECASE)
        if match and match.group(1).strip():
            value = re.sub(r"\s+", " ", match.group(1)).strip(" :")
            return value or None
        if re.search(rf"\b{re.escape(label)}\b\s*:?\s*$", line, re.IGNORECASE):
            if index + 1 < len(lines):
                value = re.sub(r"\s+", " ", lines[index + 1]).strip(" :")
                return value or None
    return None


def _extract_mes_pagamento(raw_text: str) -> str | None:
    lines = [line.strip() for line in raw_text.splitlines()]
    for index, line in enumerate(lines):
        if "Mês de Pagamento" in line or "Mes de Pagamento" in line:
            inline = re.search(r"(\d{2}/\d{4})", line)
            if inline:
                return inline.group(1)
            if index + 1 < len(lines):
                next_line = lines[index + 1]
                next_match = re.search(r"(\d{2}/\d{4})", next_line)
                if next_match:
                    return next_match.group(1)
    return None


def _extract_data_disponibilidade_saque(raw_text: str) -> str | None:
    match = re.search(
        r"Data de disponibilidade para saque:\s*(\d{2}/\d{2}/\d{4})",
        raw_text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _extract_juros_mora_selic(raw_text: str) -> float | None:
    match = re.search(
        r"Juros\s+de\s+mora/SELIC\s*:\s*([0-9.\s]+,\d{2}|0(?:,00)?)",
        raw_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _parse_brazilian_decimal(match.group(1))


def _parse_brazilian_decimal(value: str) -> float | None:
    cleaned = re.sub(r"\s+", "", value).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None
