"""CLI enxuta: cálculo a partir de demonstrativo + comprovante já existentes."""

from __future__ import annotations

import argparse
import sys

import structlog

from recupere_monday.config import get_settings
from recupere_monday.logging_config import setup_logging
from recupere_monday.pipeline.build_requerente_payloads import (
    RequerentePayloadsOrchestrator,
)
from recupere_monday.pipeline.export_calculos_excel import CalculosExcelExporter
from recupere_monday.pipeline.extract_comprovantes_ir import ComprovantesIROrchestrator
from recupere_monday.pipeline.process_calculos_ia import CalculosIAOrchestrator
from recupere_monday.pipeline.test_calculo_ia import CalculoIATestOrchestrator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cálculo IR a partir de demonstrativo + comprovante já existentes "
            "(IR → payloads → IA/RRA/Selic → Monday → Excel)."
        ),
    )
    parser.add_argument(
        "--extract-ir-comprovantes",
        action="store_true",
        help="Valida quais comprovantes possuem IR retido",
    )
    parser.add_argument(
        "--build-requerente-payloads",
        action="store_true",
        help="Gera pasta individual por requerente e planilha de controle",
    )
    parser.add_argument(
        "--processar-calculos-ia",
        action="store_true",
        help="Processa cálculos na IA (RRA/Selic/Monday) e exporta Excel",
    )
    parser.add_argument(
        "--exportar-resultados-excel",
        action="store_true",
        help="Gera planilha executiva com os JSONs finais de cálculo",
    )
    parser.add_argument(
        "--test-calculo-ia",
        action="store_true",
        help="Faz uma chamada teste à IA com PDFs de um requerente com IR retido",
    )
    parser.add_argument(
        "--force-reprocess-calculos",
        action="store_true",
        help="Reprocessa cálculos mesmo se já existirem como processados no MySQL",
    )
    parser.add_argument(
        "--limit",
        "--limite",
        dest="limit",
        type=int,
        default=None,
        help="Limita a quantidade de registros processados no cálculo IA",
    )
    parser.add_argument(
        "--log-verbose",
        action="store_true",
        help="Logs técnicos completos (equivalente a LOG_STYLE=verbose)",
    )
    return parser


def _configure_console_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    _configure_console_output()
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    log_style = "verbose" if args.log_verbose else settings.log_style
    setup_logging(settings.log_level, style=log_style)
    from recupere_monday.http_proxy import configure_proxy_from_settings

    configure_proxy_from_settings(settings)
    log = structlog.get_logger("cli_calculos")

    if args.extract_ir_comprovantes:
        log.info("extract_ir_comprovantes_starting")
        try:
            ir_result = ComprovantesIROrchestrator(settings).run()
        except Exception as exc:
            log.exception("extract_ir_comprovantes_crashed", error=str(exc))
            return 1
        log.info(
            "extract_ir_comprovantes_summary",
            total=ir_result.total_status_files,
            extracted=ir_result.extracted,
            with_ir=ir_result.with_ir,
            without_ir=ir_result.without_ir,
            skipped=ir_result.skipped,
            failed=ir_result.failed,
            output=str(ir_result.output_path),
        )
        return 1 if ir_result.failed > 0 else 0

    if args.build_requerente_payloads:
        log.info("build_requerente_payloads_starting")
        try:
            payloads_result = RequerentePayloadsOrchestrator(settings).run()
        except Exception as exc:
            log.exception("build_requerente_payloads_crashed", error=str(exc))
            return 1
        log.info(
            "build_requerente_payloads_summary",
            total_registros=payloads_result.total_registros,
            total_requerentes=payloads_result.total_requerentes,
            total_requerentes_aptos=payloads_result.total_requerentes_aptos,
            total_requerentes_inaptos=payloads_result.total_requerentes_inaptos,
            failed=payloads_result.failed,
            output_dir=str(payloads_result.output_dir),
        )
        return 1 if payloads_result.failed > 0 else 0

    if args.test_calculo_ia:
        log.info("test_calculo_ia_starting")
        try:
            calculo_result = CalculoIATestOrchestrator(settings).run()
        except Exception as exc:
            log.exception("test_calculo_ia_crashed", error=str(exc))
            return 1
        return 1 if calculo_result.failed > 0 or calculo_result.processed == 0 else 0

    if args.processar_calculos_ia:
        log.info("processar_calculos_ia_starting", limit=args.limit)
        try:
            calculos_result = CalculosIAOrchestrator(settings).run(
                limit=args.limit,
                force_reprocess=args.force_reprocess_calculos,
            )
        except Exception as exc:
            log.exception("processar_calculos_ia_crashed", error=str(exc))
            return 1
        log.info(
            "processar_calculos_ia_summary",
            total_registros=calculos_result.total_registros,
            elegiveis=calculos_result.elegiveis,
            processed=calculos_result.processed,
            skipped=calculos_result.skipped,
            failed=calculos_result.failed,
            monday_encaminhados=calculos_result.monday_encaminhados,
        )
        export_result = CalculosExcelExporter(settings).run()
        return 1 if calculos_result.failed > 0 or export_result.failed > 0 else 0

    if args.exportar_resultados_excel:
        log.info("exportar_resultados_excel_starting")
        try:
            export_result = CalculosExcelExporter(settings).run()
        except Exception as exc:
            log.exception("exportar_resultados_excel_crashed", error=str(exc))
            return 1
        return 1 if export_result.failed > 0 else 0

    _build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
