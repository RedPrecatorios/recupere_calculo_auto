"""Configuração de logs: modo simples (padrão) ou verbose."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any

import structlog

# Eventos técnicos demais para o dia a dia — só aparecem em LOG_STYLE=verbose
# ou com LOG_LEVEL=DEBUG.
_SIMPLE_DROP_EVENTS = frozenset(
    {
        "bb_proxy_selected",
        "caixa_proxy_selected",
        "caixa_search_account",
        "bb_using_direct_preferred",
        "bb_comprovante_proxy_failed",
        "caixa_comprovante_proxy_failed",
        "caixa_comprovante_attempt_failed",
        "bb_comprovante_attempt_failed",
        "bb_comprovante_period_not_found",
        "caixa_comprovante_skipped",
        "bb_comprovante_skipped",
        "full_flow_generated_dir_removed",
        "full_flow_generated_file_removed",
        "full_flow_generated_path_not_removed",
        "template_loaded",
        "template_skipped",
        "requisitorio_template_loaded",
        "requisitorio_template_skipped",
        "full_flow_collect_caixa_summary",
        "full_flow_collect_bb_summary",
        "full_flow_download_summary",
        "full_flow_detect_demonstrativos_summary",
        "full_flow_extract_demonstrativos_summary",
        "full_flow_extract_ir_summary",
        "full_flow_build_requerentes_summary",
        "full_flow_processar_calculos_ia_summary",
        "full_flow_exportar_resultados_excel_summary",
        "full_flow_collect_caixa_partial_failures_continue",
        "full_flow_collect_bb_partial_failures_continue",
        "operational_db_comprovante_status_persist_failed",
    }
)

_LEVEL_ICON = {
    "debug": "-",
    "info": ">",
    "warning": "!",
    "error": "x",
    "critical": "x",
    "exception": "x",
}


def setup_logging(level: str = "INFO", style: str = "simple") -> None:
    """
    style=simple  → linhas curtas em português (padrão)
    style=verbose → formato structlog completo (debug de integração)
    """
    style_norm = (style or "simple").strip().lower()
    if style_norm in {"verbose", "full", "debug", "detailed"}:
        style_norm = "verbose"
    else:
        style_norm = "simple"

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Silencia retries barulhentos do urllib3 (ex.: Tunnel 403).
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.WARNING)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%H:%M:%S"),
    ]
    if style_norm == "simple":
        processors.append(_drop_noisy_events)
        processors.append(_simple_message)
        processors.append(_simple_renderer)
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _drop_noisy_events(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    event = str(event_dict.get("event") or "")
    if event in _SIMPLE_DROP_EVENTS:
        raise structlog.DropEvent
    # "não encontrado" em massa: mantém se houver identificação do caso.
    if event in {
        "caixa_comprovante_not_found",
        "bb_comprovante_not_found",
    } and not (data.get("processo") or data.get("nome") or data.get("conta")):
        raise structlog.DropEvent
    return event_dict


def _simple_message(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    event = str(event_dict.get("event") or "")
    event_dict["message"] = _friendly_message(event, event_dict)
    return event_dict


def _simple_renderer(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> str:
    level = str(event_dict.get("level") or "info")
    icon = _LEVEL_ICON.get(level, "›")
    ts = event_dict.get("timestamp") or datetime.now().strftime("%H:%M:%S")
    message = event_dict.get("message") or event_dict.get("event") or ""
    return f"{ts}  {icon}  {message}"


def _short(value: Any, max_len: int = 64) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _mask_doc(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11:
        return f"{digits[:3]}.***.***-{digits[-2:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.***.***/****-{digits[-2:]}"
    return digits or "—"


def demonstrativo_log_context(data: dict[str, Any] | None) -> dict[str, Any]:
    """Campos úteis do caso para logs de coleta de comprovante."""
    data = data or {}
    return {
        "processo": data.get("processo_pasta") or None,
        "requisicao": data.get("processo_requisicao") or None,
        "nome": data.get("beneficiario_nome") or None,
        "cpf": _mask_doc(data.get("beneficiario_cpf_cnpj")),
        "conta": data.get("conta_deposito") or None,
        "agencia": data.get("agencia") or None,
        "banco": (str(data.get("banco") or "").upper() or None),
        "mes_pagamento": data.get("mes_pagamento") or None,
    }


def _case_label(data: dict[str, Any]) -> str:
    """Monta 'proc | nome | conta' a partir do evento de log."""
    parts: list[str] = []
    processo = data.get("processo") or data.get("processo_pasta")
    nome = data.get("nome") or data.get("beneficiario_nome")
    conta = data.get("conta") or data.get("conta_deposito")
    requisicao = data.get("requisicao") or data.get("processo_requisicao")
    cpf = data.get("cpf")
    if processo:
        parts.append(_short(processo, 44))
    if nome:
        parts.append(_short(nome, 36))
    if conta:
        parts.append(f"conta={conta}")
    elif requisicao:
        parts.append(f"req={_short(requisicao, 28)}")
    if cpf and cpf != "—":
        parts.append(f"cpf={cpf}")
    return " | ".join(parts) if parts else _short(data.get("file"), 50)


def _friendly_message(event: str, data: dict[str, Any]) -> str:
    # --- fluxo completo ---
    if event == "full_flow_starting":
        return (
            f"Iniciando fluxo"
            f"{_opt(' pasta Drive', data.get('drive_date_folder'))}"
            f"{_opt(' limite', data.get('limit'))}"
        )
    if event == "full_flow_finished":
        return "Fluxo concluido"
    if event == "full_flow_summary_written":
        return f"Resumo salvo em {_short(data.get('path'), 80)}"
    if event == "full_flow_no_files_selected":
        return (
            "Nenhum PDF na pasta do Drive"
            f"{_opt('', data.get('drive_date_folder'), prefix=' (')}"
            + (")" if data.get("drive_date_folder") else "")
        )
    if event.endswith("_crashed"):
        etapa = event.replace("full_flow_", "").replace("_crashed", "").replace("_", " ")
        return f"Parou na etapa '{etapa}': {_short(data.get('error'), 120)}"

    # --- Caixa ---
    if event == "caixa_comprovantes_finished":
        return (
            "Caixa concluida - "
            f"{data.get('collected', 0)} OK | "
            f"{data.get('not_found', 0)} nao encontrado | "
            f"{data.get('failed', 0)} falha | "
            f"{data.get('cnpj_skipped', 0)} CNPJ ignorado | "
            f"{data.get('skipped', 0)} pulados"
        )
    if event == "comprovante_caso_iniciando":
        banco = data.get("banco") or "?"
        return f"Consultando {banco} - {_case_label(data)}"
    if event == "caixa_comprovante_collected":
        label = _case_label(data) or _short(data.get("file") or data.get("pdf"), 70)
        return f"Caixa OK - {label}"
    if event == "caixa_comprovante_failed":
        return (
            f"Caixa falhou [{data.get('error_class')}] - "
            f"{_case_label(data) or _short(data.get('file'), 40)} | "
            f"{_short(data.get('error'), 60)}"
        )
    if event == "caixa_comprovante_not_found":
        return f"Caixa nao localizou - {_case_label(data)}"
    if event == "caixa_switching_to_direct_after_proxy_blocks":
        return "Caixa: proxy bloqueado -> tentando sem proxy"
    if event == "caixa_comprovantes_input_missing":
        return f"Pasta de entrada Caixa nao existe: {_short(data.get('path'), 80)}"

    # --- BB ---
    if event == "bb_comprovantes_finished":
        return (
            "BB concluido - "
            f"{data.get('collected', 0)} OK | "
            f"{data.get('not_found', 0)} nao encontrado | "
            f"{data.get('failed', 0)} falha | "
            f"{data.get('cnpj_skipped', 0)} CNPJ ignorado | "
            f"{data.get('skipped', 0)} pulados"
        )
    if event == "bb_comprovante_collected":
        label = _case_label(data) or _short(data.get("file") or data.get("pdf"), 70)
        return f"BB OK - {label}"
    if event == "bb_comprovante_failed":
        return (
            f"BB falhou [{data.get('error_class')}] - "
            f"{_case_label(data) or _short(data.get('file'), 40)} | "
            f"{_short(data.get('error'), 60)}"
        )
    if event == "bb_comprovante_not_found":
        return f"BB nao localizou - {_case_label(data)}"
    if event == "bb_switching_to_direct_after_proxy_blocks":
        return "BB: proxy bloqueado -> tentando sem proxy"
    if event == "bb_comprovantes_input_missing":
        return f"Pasta de entrada BB nao existe: {_short(data.get('path'), 80)}"

    if event == "reprocess_fluxo_cpf_item_starting":
        return (
            f"Reprocesso CPF {data.get('idx')}/{data.get('total')} - "
            f"{_short(data.get('processo_pasta'), 44)}"
            f"{_opt(' banco', data.get('banco'))}"
            f"{_opt(' falhas', data.get('falhas'))}"
            f"{_opt(' drive', data.get('drive_date_folder'))}"
        )
    if event == "flow_by_process_item_starting":
        return (
            f"Fluxo do processo - {_short(data.get('processo_pasta'), 44)}"
            f"{_opt(' drive', data.get('drive_date_folder'))}"
        )
    if event == "flow_all_dates_starting":
        return (
            f"Todas as datas Drive: {data.get('total_datas')} pastas "
            f"({data.get('oldest')} → {data.get('newest')})"
        )
    if event == "flow_all_dates_item_starting":
        return (
            f"Data Drive {data.get('idx')}/{data.get('total')} - "
            f"{data.get('drive_date_folder')}"
        )
    if event == "flow_by_process_stage_crashed":
        return (
            f"Etapa '{data.get('stage')}' falhou - "
            f"{_short(data.get('processo_pasta'), 44)} | "
            f"processo ignorado, o lote continua"
            f"{_opt('', _short(data.get('error'), 60), prefix=' | ')}"
        )
    if event == "flow_by_process_item_crashed":
        return (
            f"Processo interrompido por erro - "
            f"{_short(data.get('processo_pasta'), 44)} | o lote continua"
            f"{_opt('', _short(data.get('error'), 60), prefix=' | ')}"
        )
    if event == "flow_all_dates_item_crashed":
        return (
            f"Data {data.get('drive_date_folder')} interrompida por erro | "
            f"as demais datas continuam"
            f"{_opt('', _short(data.get('error'), 60), prefix=' | ')}"
        )
    if event == "fluxo_checkpoint_resuming":
        return (
            f"Retomando fluxo do checkpoint "
            f"({data.get('completed', 0)} processos ja concluidos)"
        )
    if event == "fluxo_checkpoint_skip":
        return (
            f"Checkpoint: pulando processo ja concluido - "
            f"{_short(data.get('processo_pasta'), 44)}"
            f"{_opt(' drive', data.get('drive_date_folder'))}"
        )
    if event == "fluxo_checkpoint_saved":
        return (
            f"Checkpoint salvo - {_short(data.get('processo_pasta'), 44)}"
            f"{_opt(' status', data.get('status'))}"
            f"{_opt(' total', data.get('completed'))}"
        )
    if event == "flow_all_dates_finished":
        return (
            f"Todas as datas concluido - {data.get('datas')} datas | "
            f"{data.get('processos')} processos | "
            f"{data.get('datas_com_falha', 0)} datas com falha"
        )
    if event == "drive_date_folders_listed":
        return (
            f"Pastas de data no Drive: {data.get('count')} "
            f"({data.get('oldest')} → {data.get('newest')})"
        )

    # --- reprocessamento ---
    if event == "reprocess_comprovantes_starting":
        return (
            "Reprocessar falhas comprovante"
            f"{_opt(' banco', data.get('banco'))}"
            f"{_opt(' error_class', data.get('error_classes'))}"
            f"{_opt(' dry_run', data.get('dry_run'))}"
        )
    if event == "reprocess_comprovantes_listed":
        return f"MySQL listou {data.get('total', 0)} coleta(s) para reprocessar"
    if event == "reprocess_comprovantes_item":
        return (
            f"Fila {data.get('banco')} | {data.get('error_class') or data.get('status')} | "
            f"proc={_short(data.get('processo'), 40)} | conta={data.get('conta')}"
        )
    if event == "reprocess_comprovantes_finished":
        return (
            "Reprocessamento concluido - "
            f"listados={data.get('listed', 0)} | casados={data.get('matched', 0)} | "
            f"via MySQL={data.get('from_mysql', 0)} | sem JSON={data.get('missing_json', 0)} | "
            f"Caixa OK={data.get('caixa_ok', 0)} | BB OK={data.get('bb_ok', 0)}"
        )
    if event == "reprocess_comprovantes_prepared":
        return (
            f"Preparados {data.get('matched', 0)} caso(s) "
            f"(local={data.get('from_local_json', 0)}, mysql={data.get('from_mysql', 0)}) "
            f"| Caixa={data.get('caixa', 0)} BB={data.get('bb', 0)}"
        )
    if event == "reprocess_comprovantes_summary":
        if data.get("dry_run"):
            return f"Dry-run: {data.get('listed', 0)} caso(s) listado(s), sem coletar"
        return (
            "Resumo reprocessamento - "
            f"OK={data.get('collected', 0)} | "
            f"Caixa nao achou={data.get('caixa_nao_achou', 0)} | "
            f"BB nao achou={data.get('bb_nao_achou', 0)} | "
            f"sem dados={data.get('missing_json', 0)}"
        )
    if event == "reprocess_comprovantes_db_missing":
        return "MySQL operacional nao configurado (AUTO_RECUPERE_DB_*)"

    # --- generico ---
    if event == "item_result":
        etapa = data.get("etapa") or "?"
        idx = data.get("idx")
        total = data.get("total")
        resultado = data.get("resultado") or ""
        detalhe = data.get("detalhe")
        progress = f"{idx}/{total}" if idx is not None and total else (str(idx) if idx else "")
        parts = [str(etapa)]
        if progress:
            parts.append(progress)
        case = _case_label(data)
        if case:
            parts.append(case)
        parts.append(str(resultado))
        if detalhe:
            parts.append(_short(detalhe, 50))
        return " | ".join(parts)

    if event == "calculo_ia_monday_duplicado":
        return (
            "Monday: registro ja existe no board, item nao criado"
            f"{_opt('', data.get('item_id'), prefix=' | item=')}"
        )
    if event == "calculo_ia_monday_dedupe_falhou":
        return (
            "Monday: nao foi possivel checar duplicidade"
            f"{_opt('', _short(data.get('error'), 60), prefix=' | ')}"
        )
    if event == "calculo_ia_monday_dedupe_sem_chave":
        return (
            "Monday: checagem de duplicidade ignorada (campos-chave ausentes "
            "no mapeamento de colunas)"
        )

    # Fallback: evento + poucos campos uteis
    skip_keys = {
        "event",
        "level",
        "timestamp",
        "logger",
        "run_id",
        "message",
        "_record",
    }
    extras = []
    for key, value in data.items():
        if key in skip_keys or value is None:
            continue
        if key in {"error", "path", "file", "pdf", "conta", "cpf_cnpj", "reason"}:
            extras.append(f"{key}={_short(value, 50)}")
        if len(extras) >= 3:
            break
    label = event.replace("_", " ")
    if extras:
        return f"{label} | " + " | ".join(extras)
    return label


def _opt(label: str, value: Any, *, prefix: str | None = None) -> str:
    if value is None or value == "":
        return ""
    if prefix is not None:
        return f"{prefix}{value}"
    return f"{label}={value}"
