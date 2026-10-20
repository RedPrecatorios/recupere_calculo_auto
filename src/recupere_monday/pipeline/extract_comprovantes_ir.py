from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from recupere_monday.config import Settings
from recupere_monday.extraction.comprovante_ir import extract_ir_from_comprovante

logger = structlog.get_logger(__name__)


@dataclass
class ComprovantesIRResult:
    total_status_files: int = 0
    extracted: int = 0
    with_ir: int = 0
    without_ir: int = 0
    skipped: int = 0
    failed: int = 0
    output_path: Path | None = None
    errors: list[str] = field(default_factory=list)


class ComprovantesIROrchestrator:
    """Valida IR retido nos comprovantes de levantamento coletados."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._status_dirs = (
            settings.comprovantes_caixa_status_output_path,
            settings.comprovantes_bb_status_output_path,
        )
        self._output_path = settings.comprovantes_ir_retido_output_path

    def run(
        self,
        *,
        process_filter: str | None = None,
        merge_existing: bool = False,
    ) -> ComprovantesIRResult:
        result = ComprovantesIRResult(output_path=self._output_path)
        records: list[dict[str, Any]] = []
        skipped_records: list[dict[str, Any]] = []

        status_files = self._status_files(process_filter=process_filter)
        result.total_status_files = len(status_files)

        for status_path in status_files:
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
                source = payload.get("source", {})
                status = payload.get("status", {})
                if not status.get("success"):
                    result.skipped += 1
                    skipped_records.append(
                        _build_skipped_record(
                            status_path,
                            payload,
                            "status_sem_sucesso",
                        )
                    )
                    continue

                pdf_path_value = status.get("pdf_path")
                if not pdf_path_value:
                    result.skipped += 1
                    skipped_records.append(
                        _build_skipped_record(
                            status_path,
                            payload,
                            "pdf_path_ausente",
                        )
                    )
                    continue

                pdf_path = Path(str(pdf_path_value))
                if not pdf_path.exists():
                    result.failed += 1
                    result.errors.append(f"{status_path.name}: PDF não encontrado: {pdf_path}")
                    continue

                banco = str(source.get("banco", "")).upper()
                ir_data = extract_ir_from_comprovante(pdf_path, banco)
                record = self._build_record(source, status, ir_data.to_dict(), pdf_path)
                records.append(record)

                result.extracted += 1
                if record["tem_ir_retido"]:
                    result.with_ir += 1
                else:
                    result.without_ir += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"{status_path.name}: {exc}")
                logger.exception(
                    "comprovante_ir_extract_failed",
                    status=str(status_path),
                    error=str(exc),
                )

        self._write_output(records, skipped_records, result, merge_existing=merge_existing)
        logger.info(
            "comprovantes_ir_finished",
            total=result.total_status_files,
            extracted=result.extracted,
            with_ir=result.with_ir,
            without_ir=result.without_ir,
            skipped=result.skipped,
            failed=result.failed,
            output=str(self._output_path),
        )
        return result

    def _status_files(self, *, process_filter: str | None = None) -> list[Path]:
        files: list[Path] = []
        for status_dir in self._status_dirs:
            if not status_dir.exists():
                continue
            files.extend(
                path
                for path in status_dir.rglob("*.json")
                if not path.name.startswith("_")
                and (process_filter is None or process_filter in path.parts)
            )
        return sorted(files)

    @staticmethod
    def _build_record(
        source: dict[str, Any],
        status: dict[str, Any],
        ir_data: dict[str, Any],
        pdf_path: Path,
    ) -> dict[str, Any]:
        comprovante_data = status.get("comprovante_data") or {}
        nome = _clean_requerente_nome(
            source.get("beneficiario_nome") or ir_data.get("requerente_nome")
        )
        cpf_cnpj = source.get("beneficiario_cpf_cnpj") or ir_data.get("requerente_cpf_cnpj")
        valor_ir = ir_data.get("valor_ir_retido") or comprovante_data.get("valor_ir")
        numero_meses_rra = (
            ir_data.get("numero_meses_rra")
            or comprovante_data.get("numero_meses")
            or comprovante_data.get("numero_meses_rra")
        )

        return {
            "processo_pasta": source.get("processo_pasta"),
            "processo_requisicao": source.get("processo_requisicao"),
            "banco": source.get("banco"),
            "requerente_nome": nome,
            "requerente_cpf_cnpj": cpf_cnpj,
            "agencia": source.get("agencia"),
            "conta_deposito": source.get("conta_deposito"),
            "tem_ir_retido": bool(ir_data.get("tem_ir_retido")),
            "valor_ir_retido": valor_ir,
            "campo_origem": ir_data.get("campo_origem"),
            "deducao_previdenciaria": ir_data.get("deducao_previdenciaria"),
            "numero_meses_rra": numero_meses_rra,
            "pdf_path": str(pdf_path),
        }

    def _write_output(
        self,
        records: list[dict[str, Any]],
        skipped_records: list[dict[str, Any]],
        result: ComprovantesIRResult,
        *,
        merge_existing: bool = False,
    ) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        if merge_existing and self._output_path.exists():
            existing = json.loads(self._output_path.read_text(encoding="utf-8"))
            existing_records = existing.get("todos", [])
            by_pdf = {
                _normalize_path(record.get("pdf_path")): record
                for record in existing_records
                if record.get("pdf_path")
            }
            by_pdf.update(
                {
                    _normalize_path(record.get("pdf_path")): record
                    for record in records
                    if record.get("pdf_path")
                }
            )
            records = list(by_pdf.values())
            skipped_records = [
                *existing.get("ignorados_detalhados", []),
                *skipped_records,
            ]
        payload = {
            "summary": {
                "total_comprovantes_analisados": len(records),
                "com_ir_retido": sum(
                    1 for record in records if record.get("tem_ir_retido") is True
                ),
                "sem_ir_retido": sum(
                    1 for record in records if record.get("tem_ir_retido") is False
                ),
                "ignorados": len(skipped_records),
                "falhas": result.failed,
            },
            "com_ir_retido": [
                record for record in records if record.get("tem_ir_retido") is True
            ],
            "sem_ir_retido": [
                record for record in records if record.get("tem_ir_retido") is False
            ],
            "ignorados_detalhados": skipped_records,
            "todos": records,
        }
        self._output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").lower()


def _clean_requerente_nome(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    for marker in ("alvará).", "Federal."):
        if marker in text:
            text = text.split(marker)[-1].strip()
    return text or None


def _build_skipped_record(
    status_path: Path,
    payload: dict[str, Any],
    motivo: str,
) -> dict[str, Any]:
    source = payload.get("source", {})
    status = payload.get("status", {})
    return {
        "motivo": motivo,
        "status_json_path": str(status_path),
        "processo_pasta": source.get("processo_pasta"),
        "processo_requisicao": source.get("processo_requisicao"),
        "banco": source.get("banco"),
        "requerente_nome": source.get("beneficiario_nome"),
        "requerente_cpf_cnpj": source.get("beneficiario_cpf_cnpj"),
        "pdf_path": status.get("pdf_path"),
        "erro": status.get("error"),
        "reason": status.get("reason"),
    }
