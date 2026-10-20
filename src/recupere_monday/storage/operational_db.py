from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import structlog

from recupere_monday.config import Settings
from recupere_monday.drive.client import DriveFile

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OperationalDBConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


class OperationalDBRepository:
    """Persistência operacional MySQL para controle de duplicidade e auditoria."""

    def __init__(self, config: OperationalDBConfig) -> None:
        self._config = config

    @classmethod
    def from_settings(cls, settings: Settings) -> OperationalDBRepository | None:
        if not settings.auto_recupere_db_enabled:
            logger.info("operational_db_disabled")
            return None
        return cls(
            OperationalDBConfig(
                host=settings.auto_recupere_db_host,
                port=settings.auto_recupere_db_port,
                database=settings.auto_recupere_db_name,
                user=settings.auto_recupere_db_user,
                password=settings.auto_recupere_db_password,
            )
        )

    def should_skip_drive_file(self, drive_file: DriveFile) -> bool:
        """Retorna True quando o mesmo arquivo do Drive já foi baixado/processado."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT last_status, drive_modified_time, local_download_path
                    FROM ar_drive_files
                    WHERE drive_file_id = %s
                    LIMIT 1
                    """,
                    (drive_file.file_id,),
                )
                row = cursor.fetchone()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_skip_check_failed",
                file_id=drive_file.file_id,
                error=str(exc),
            )
            return False

        if not row:
            return False

        local_download_path = row.get("local_download_path")
        if local_download_path and not Path(str(local_download_path)).exists():
            return False

        same_modified_time = _same_datetime(
            _parse_drive_datetime(drive_file.modified_time),
            row["drive_modified_time"],
        )
        return same_modified_time and row["last_status"] in {
            "downloaded",
            "processing",
            "completed",
        }

    def apply_schema(self, schema_path: Path) -> int:
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema SQL não encontrado: {schema_path}")

        statements = _split_sql_statements(schema_path.read_text(encoding="utf-8"))
        applied = 0
        with self._connect() as conn:
            cursor = conn.cursor()
            for statement in statements:
                cursor.execute(statement)
                applied += 1
            conn.commit()
            cursor.close()
        return applied

    def mark_drive_file_seen(
        self,
        drive_file: DriveFile,
        *,
        drive_date_folder: str | None,
    ) -> None:
        self._upsert_drive_file(
            drive_file,
            drive_date_folder=drive_date_folder,
            local_download_path=None,
            last_status="new",
            last_error_message=None,
            mark_downloaded=False,
        )

    def mark_drive_file_downloaded(
        self,
        drive_file: DriveFile,
        *,
        local_path: Path,
        drive_date_folder: str | None,
    ) -> None:
        self._upsert_drive_file(
            drive_file,
            drive_date_folder=drive_date_folder,
            local_download_path=local_path,
            last_status="downloaded",
            last_error_message=None,
            mark_downloaded=True,
        )

    def mark_drive_file_failed(
        self,
        drive_file: DriveFile,
        *,
        error_message: str,
        drive_date_folder: str | None,
    ) -> None:
        self._upsert_drive_file(
            drive_file,
            drive_date_folder=drive_date_folder,
            local_download_path=None,
            last_status="failed",
            last_error_message=error_message,
            mark_downloaded=False,
        )

    def persist_requerente_payload(
        self,
        *,
        payload: dict[str, Any],
        payload_path: Path,
        classificacao: str,
    ) -> None:
        """Persiste requerente, requisições, validações e artefatos de um payload."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                for registro in payload.get("registros", []):
                    processo_id = self._ensure_processo(cursor, registro)
                    requerente_id = self._ensure_requerente(cursor, payload, registro)
                    requisicao_id = self._ensure_requisicao(
                        cursor,
                        registro,
                        processo_id=processo_id,
                        requerente_id=requerente_id,
                        classificacao=classificacao,
                    )
                    payload_artifact_id = self._upsert_artifact(
                        cursor,
                        artifact_type="payload_calculo_json",
                        storage_path=payload_path,
                        requisicao_id=requisicao_id,
                        metadata={"tipo_payload": payload.get("tipo_payload")},
                    )
                    self._upsert_comprovante_coleta(cursor, registro, requisicao_id)
                    self._upsert_ir_validacao(
                        cursor,
                        registro,
                        requisicao_id,
                        classificacao=classificacao,
                        payload=payload,
                    )
                    self._upsert_artifact(
                        cursor,
                        artifact_type="demonstrativo_pdf",
                        storage_path=registro.get("demonstrativo", {}).get(
                            "pdf_extraido_path"
                        ),
                        requisicao_id=requisicao_id,
                    )
                    self._upsert_artifact(
                        cursor,
                        artifact_type="demonstrativo_campos_json",
                        storage_path=registro.get("demonstrativo", {}).get("json_path"),
                        requisicao_id=requisicao_id,
                    )
                    self._upsert_artifact(
                        cursor,
                        artifact_type="comprovante_pdf",
                        storage_path=registro.get("comprovante", {}).get("pdf_path"),
                        requisicao_id=requisicao_id,
                    )
                    self._update_processo_totals(cursor, processo_id)
                    logger.debug(
                        "operational_db_payload_record_persisted",
                        requisicao_id=requisicao_id,
                        payload_artifact_id=payload_artifact_id,
                    )
                conn.commit()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_payload_persist_failed",
                payload=str(payload_path),
                error=str(exc),
            )

    def persist_calculation_result(
        self,
        *,
        output_payload: dict[str, Any],
        output_path: Path,
        registro: dict[str, Any],
        openai_model: str,
    ) -> None:
        """Persiste o JSON final de cálculo em ar_calculos_ia/ar_resultados_calculo."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                processo_id = self._ensure_processo(cursor, registro)
                requerente_id = self._ensure_requerente(cursor, output_payload, registro)
                requisicao_id = self._ensure_requisicao(
                    cursor,
                    registro,
                    processo_id=processo_id,
                    requerente_id=requerente_id,
                    classificacao="apto_com_ir",
                )
                artifact_id = self._upsert_artifact(
                    cursor,
                    artifact_type="calculo_ia_json",
                    storage_path=output_path,
                    requisicao_id=requisicao_id,
                    metadata={
                        "registro_id": output_payload.get("registro_id"),
                        "tipo_processamento": output_payload.get("tipo_processamento"),
                    },
                )
                calculo_id = self._upsert_calculo_ia(
                    cursor,
                    output_payload,
                    requisicao_id=requisicao_id,
                    artifact_id=artifact_id,
                    openai_model=openai_model,
                )
                self._upsert_resultado_calculo(
                    cursor,
                    output_payload,
                    calculo_id=calculo_id,
                    requisicao_id=requisicao_id,
                )
                cursor.execute(
                    """
                    UPDATE ar_processos
                    SET status = 'completed',
                        completed_at = COALESCE(completed_at, UTC_TIMESTAMP(6))
                    WHERE id = %s
                    """,
                    (processo_id,),
                )
                conn.commit()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_calculation_persist_failed",
                output=str(output_path),
                error=str(exc),
            )

    def start_pipeline_run(
        self,
        *,
        run_uuid: str,
        command_line: str | None = None,
        drive_date_folder: str | None = None,
        limit_processes: int | None = None,
        processar_calculos_ia: bool = False,
    ) -> int | None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO ar_pipeline_runs (
                        run_uuid, command_line, drive_date_folder,
                        limit_processes, processar_calculos_ia, status
                    ) VALUES (%s, %s, %s, %s, %s, 'running')
                    """,
                    (
                        run_uuid,
                        command_line,
                        drive_date_folder,
                        limit_processes,
                        bool(processar_calculos_ia),
                    ),
                )
                run_id = int(cursor.lastrowid)
                conn.commit()
                cursor.close()
                return run_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("operational_db_start_run_failed", error=str(exc))
            return None

    def finish_pipeline_run(
        self,
        *,
        run_uuid: str,
        status: str,
        summary: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE ar_pipeline_runs
                    SET status = %s,
                        finished_at = UTC_TIMESTAMP(6),
                        summary_json = %s,
                        error_message = %s
                    WHERE run_uuid = %s
                    """,
                    (
                        status,
                        _json_dumps(summary or {}),
                        error_message,
                        run_uuid,
                    ),
                )
                conn.commit()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_finish_run_failed",
                run_uuid=run_uuid,
                error=str(exc),
            )

    def log_falha_operacional(
        self,
        *,
        stage_name: str,
        error_class: str,
        error_message: str | None = None,
        run_uuid: str | None = None,
        processo_pasta: str | None = None,
        processo_requisicao: str | None = None,
        banco: str | None = None,
        conta_deposito: str | None = None,
        registro_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO ar_falhas_operacionais (
                        run_uuid, stage_name, processo_pasta, processo_requisicao,
                        banco, conta_deposito, registro_id, error_class,
                        error_message, context_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_uuid,
                        stage_name,
                        processo_pasta,
                        processo_requisicao,
                        banco,
                        conta_deposito,
                        registro_id,
                        error_class,
                        (error_message or "")[:2000] or None,
                        _json_dumps(context or {}),
                    ),
                )
                conn.commit()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_log_falha_failed",
                stage=stage_name,
                error=str(exc),
            )

    def persist_comprovante_status(
        self,
        *,
        demonstrativo_data: dict[str, Any],
        banco: str,
        status_payload: dict[str, Any],
        run_uuid: str | None = None,
    ) -> None:
        """Persiste resultado da coleta (sucesso ou falha) a partir do JSON de campos."""
        try:
            registro = {
                "id": (
                    f"{demonstrativo_data.get('processo_pasta')}|"
                    f"{demonstrativo_data.get('processo_requisicao')}|"
                    f"{demonstrativo_data.get('conta_deposito')}"
                ),
                "demonstrativo": demonstrativo_data,
                "comprovante": status_payload,
            }
            success = bool(status_payload.get("success")) and not bool(
                status_payload.get("skipped")
            )
            error_class = str(
                status_payload.get("error_class")
                or ("success" if success else "unknown")
            )
            reason = str(status_payload.get("reason") or "")
            with self._connect() as conn:
                cursor = conn.cursor()
                processo_id = self._ensure_processo(cursor, registro)
                requerente_id = self._ensure_requerente(
                    cursor,
                    {
                        "requerente": {
                            "nome": demonstrativo_data.get("beneficiario_nome"),
                            "cpf_cnpj": demonstrativo_data.get("beneficiario_cpf_cnpj"),
                        },
                        "requerente_key": demonstrativo_data.get("beneficiario_cpf_cnpj"),
                    },
                    registro,
                )
                if reason == "cnpj_ignorado":
                    classificacao = "ignorado_cnpj"
                elif reason == "pdf_already_exists" or error_class == "skipped":
                    classificacao = "novo"
                elif success:
                    classificacao = "apto_com_ir"
                elif error_class == "not_found":
                    classificacao = "inapto_falha_comprovante"
                else:
                    classificacao = "inapto_falha_comprovante"
                requisicao_id = self._ensure_requisicao(
                    cursor,
                    registro,
                    processo_id=processo_id,
                    requerente_id=requerente_id,
                    classificacao=classificacao,
                )
                self._upsert_comprovante_coleta(cursor, registro, requisicao_id)
                conn.commit()
                cursor.close()

            if (
                not success
                and error_class not in {"skipped", "success"}
                and reason != "cnpj_ignorado"
            ):
                self.log_falha_operacional(
                    stage_name=f"collect_{banco.lower()}",
                    error_class=error_class,
                    error_message=str(status_payload.get("error") or ""),
                    run_uuid=run_uuid,
                    processo_pasta=str(demonstrativo_data.get("processo_pasta") or ""),
                    processo_requisicao=str(
                        demonstrativo_data.get("processo_requisicao") or ""
                    ),
                    banco=banco,
                    conta_deposito=str(demonstrativo_data.get("conta_deposito") or ""),
                    context=status_payload,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_comprovante_status_persist_failed",
                banco=banco,
                error=str(exc),
            )

    def persist_calculation_failure(
        self,
        *,
        registro: dict[str, Any],
        error_message: str,
        error_class: str = "unknown",
        output_path: Path | None = None,
        run_uuid: str | None = None,
    ) -> None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                processo_id = self._ensure_processo(cursor, registro)
                requerente_id = self._ensure_requerente(cursor, {}, registro)
                requisicao_id = self._ensure_requisicao(
                    cursor,
                    registro,
                    processo_id=processo_id,
                    requerente_id=requerente_id,
                    classificacao="inapto_falha_comprovante",
                )
                artifact_id = None
                if output_path is not None:
                    artifact_id = self._upsert_artifact(
                        cursor,
                        artifact_type="calculo_ia_json",
                        storage_path=output_path,
                        requisicao_id=requisicao_id,
                    )
                cursor.execute(
                    """
                    INSERT INTO ar_calculos_ia (
                        requisicao_id, attempt_number, status, output_artifact_id,
                        error_message, error_class, processed_at
                    ) VALUES (%s, 1, 'falha', %s, %s, %s, UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE
                        status = 'falha',
                        output_artifact_id = COALESCE(VALUES(output_artifact_id), output_artifact_id),
                        error_message = VALUES(error_message),
                        error_class = VALUES(error_class),
                        processed_at = VALUES(processed_at)
                    """,
                    (
                        requisicao_id,
                        artifact_id,
                        error_message[:2000],
                        error_class,
                    ),
                )
                conn.commit()
                cursor.close()
            demo = registro.get("demonstrativo") or {}
            self.log_falha_operacional(
                stage_name="processar_calculos_ia",
                error_class=error_class,
                error_message=error_message,
                run_uuid=run_uuid,
                processo_pasta=str(demo.get("processo_pasta") or ""),
                processo_requisicao=str(demo.get("processo_requisicao") or ""),
                banco=str(demo.get("banco") or ""),
                conta_deposito=str(demo.get("conta_deposito") or ""),
                registro_id=str(registro.get("id") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_calculation_failure_persist_failed",
                error=str(exc),
            )

    def should_skip_calculo(self, registro: dict[str, Any]) -> bool:
        """True se a requisição já tem cálculo processado no MySQL."""
        identity = _requisicao_identity(registro)
        try:
            with self._connect() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT ci.status AS calculo_status
                    FROM ar_processos p
                    JOIN ar_requisicoes req ON req.processo_id = p.id
                    JOIN ar_calculos_ia ci ON ci.requisicao_id = req.id
                    WHERE p.processo_pasta = %s
                      AND req.processo_requisicao = %s
                      AND req.banco = %s
                      AND req.conta_deposito = %s
                      AND ci.status = 'processado'
                    ORDER BY ci.id DESC
                    LIMIT 1
                    """,
                    (
                        identity["processo_pasta"],
                        identity["processo_requisicao"],
                        identity["banco"],
                        identity["conta_deposito"],
                    ),
                )
                row = cursor.fetchone()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_skip_calculo_check_failed",
                registro_id=registro.get("id"),
                error=str(exc),
            )
            return False
        return bool(row)

    def find_monday_envio(
        self,
        *,
        registro_id: str,
        monday_board_id: int,
    ) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT *
                    FROM ar_monday_envios
                    WHERE registro_id = %s
                      AND monday_board_id = %s
                    LIMIT 1
                    """,
                    (registro_id, monday_board_id),
                )
                row = cursor.fetchone()
                cursor.close()
                return dict(row) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_monday_lookup_failed",
                registro_id=registro_id,
                error=str(exc),
            )
            return None

    def next_monday_envio_id(
        self,
        *,
        monday_board_id: int,
        configured_last_id: int = 0,
    ) -> int:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(monday_envio_id), 0)
                    FROM ar_monday_envios
                    WHERE monday_board_id = %s
                    """,
                    (monday_board_id,),
                )
                row = cursor.fetchone()
                cursor.close()
                db_last = int(row[0] or 0) if row else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_monday_next_id_failed",
                error=str(exc),
            )
            db_last = 0
        return max(db_last, int(configured_last_id or 0)) + 1

    def reserve_monday_envio(
        self,
        *,
        registro_id: str,
        monday_board_id: int,
        monday_group_id: str,
        monday_envio_id: int,
        registro: dict[str, Any],
        item_name: str,
        campos: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Reserva linha pending antes do create_item.
        Se já existir enviado com item_id, devolve esse registro (não reenvia).
        """
        identity = _requisicao_identity(registro)
        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT *
                FROM ar_monday_envios
                WHERE registro_id = %s AND monday_board_id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (registro_id, monday_board_id),
            )
            existing = cursor.fetchone()
            if (
                existing
                and existing.get("status") == "enviado"
                and existing.get("monday_item_id")
            ):
                conn.commit()
                cursor.close()
                return dict(existing)

            requisicao_id = self._lookup_requisicao_id(cursor, identity)
            if existing:
                cursor.execute(
                    """
                    UPDATE ar_monday_envios
                    SET status = 'pending',
                        monday_group_id = %s,
                        monday_envio_id = COALESCE(monday_envio_id, %s),
                        item_name = %s,
                        processo_pasta = %s,
                        processo_requisicao = %s,
                        banco = %s,
                        conta_deposito = %s,
                        requisicao_id = COALESCE(requisicao_id, %s),
                        campos_json = %s,
                        error_message = NULL,
                        motivo = 'reserva_antes_create_item'
                    WHERE id = %s
                    """,
                    (
                        monday_group_id,
                        monday_envio_id,
                        item_name,
                        identity["processo_pasta"],
                        identity["processo_requisicao"],
                        identity["banco"],
                        identity["conta_deposito"],
                        requisicao_id,
                        _json_dumps(campos or {}),
                        existing["id"],
                    ),
                )
                envio_id = int(existing["id"])
            else:
                cursor.execute(
                    """
                    INSERT INTO ar_monday_envios (
                        registro_id, requisicao_id, processo_pasta,
                        processo_requisicao, banco, conta_deposito,
                        monday_board_id, monday_group_id, monday_envio_id,
                        item_name, status, motivo, campos_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                    """,
                    (
                        registro_id,
                        requisicao_id,
                        identity["processo_pasta"],
                        identity["processo_requisicao"],
                        identity["banco"],
                        identity["conta_deposito"],
                        monday_board_id,
                        monday_group_id,
                        monday_envio_id,
                        item_name,
                        "reserva_antes_create_item",
                        _json_dumps(campos or {}),
                    ),
                )
                envio_id = int(cursor.lastrowid)
            conn.commit()
            cursor.execute(
                "SELECT * FROM ar_monday_envios WHERE id = %s LIMIT 1",
                (envio_id,),
            )
            row = cursor.fetchone()
            cursor.close()
            return dict(row) if row else {"id": envio_id, "status": "pending"}

    def record_monday_duplicado(
        self,
        *,
        registro_id: str,
        monday_board_id: int,
        monday_group_id: str,
        monday_item_id: str,
        item_name: str,
        registro: dict[str, Any],
        campos: dict[str, Any] | None = None,
    ) -> None:
        """
        Registra que o item já existia no board Monday.
        Não consome monday_envio_id: a numeração segue para o próximo envio real.
        """
        identity = _requisicao_identity(registro)
        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id
                FROM ar_monday_envios
                WHERE registro_id = %s AND monday_board_id = %s
                LIMIT 1
                """,
                (registro_id, monday_board_id),
            )
            existing = cursor.fetchone()
            requisicao_id = self._lookup_requisicao_id(cursor, identity)
            if existing:
                cursor.execute(
                    """
                    UPDATE ar_monday_envios
                    SET status = 'enviado',
                        monday_item_id = %s,
                        monday_group_id = %s,
                        item_name = %s,
                        requisicao_id = COALESCE(requisicao_id, %s),
                        campos_json = %s,
                        error_message = NULL,
                        enviado_at = COALESCE(enviado_at, UTC_TIMESTAMP(6)),
                        motivo = 'item_ja_existe_no_board_monday'
                    WHERE id = %s
                    """,
                    (
                        str(monday_item_id),
                        monday_group_id,
                        item_name,
                        requisicao_id,
                        _json_dumps(campos or {}),
                        existing["id"],
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO ar_monday_envios (
                        registro_id, requisicao_id, processo_pasta,
                        processo_requisicao, banco, conta_deposito,
                        monday_board_id, monday_group_id, monday_item_id,
                        item_name, status, motivo, campos_json, enviado_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'enviado', 'item_ja_existe_no_board_monday', %s,
                        UTC_TIMESTAMP(6)
                    )
                    """,
                    (
                        registro_id,
                        requisicao_id,
                        identity["processo_pasta"],
                        identity["processo_requisicao"],
                        identity["banco"],
                        identity["conta_deposito"],
                        monday_board_id,
                        monday_group_id,
                        str(monday_item_id),
                        item_name,
                        _json_dumps(campos or {}),
                    ),
                )
            conn.commit()
            cursor.close()

    def mark_monday_enviado(
        self,
        *,
        registro_id: str,
        monday_board_id: int,
        monday_item_id: str,
        monday_envio_id: int,
        item_name: str,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE ar_monday_envios
                SET status = 'enviado',
                    monday_item_id = %s,
                    monday_envio_id = %s,
                    item_name = %s,
                    enviado_at = UTC_TIMESTAMP(6),
                    error_message = NULL,
                    motivo = 'calculo_efetuado_com_imposto_indevido_a_restituir'
                WHERE registro_id = %s
                  AND monday_board_id = %s
                """,
                (
                    str(monday_item_id),
                    monday_envio_id,
                    item_name,
                    registro_id,
                    monday_board_id,
                ),
            )
            conn.commit()
            cursor.close()

    def mark_monday_falha(
        self,
        *,
        registro_id: str,
        monday_board_id: int,
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE ar_monday_envios
                SET status = 'falha',
                    error_message = %s,
                    motivo = 'falha_create_item'
                WHERE registro_id = %s
                  AND monday_board_id = %s
                """,
                (error_message[:2000], registro_id, monday_board_id),
            )
            conn.commit()
            cursor.close()

    def _lookup_requisicao_id(
        self,
        cursor: Any,
        identity: dict[str, str],
    ) -> int | None:
        cursor.execute(
            """
            SELECT req.id
            FROM ar_processos p
            JOIN ar_requisicoes req ON req.processo_id = p.id
            WHERE p.processo_pasta = %s
              AND req.processo_requisicao = %s
              AND req.banco = %s
              AND req.conta_deposito = %s
            LIMIT 1
            """,
            (
                identity["processo_pasta"],
                identity["processo_requisicao"],
                identity["banco"],
                identity["conta_deposito"],
            ),
        )
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return int(row["id"])
        return int(row[0])

    def _upsert_drive_file(
        self,
        drive_file: DriveFile,
        *,
        drive_date_folder: str | None,
        local_download_path: Path | None,
        last_status: str,
        last_error_message: str | None,
        mark_downloaded: bool,
    ) -> None:
        try:
            modified_time = _parse_drive_datetime(drive_file.modified_time)
            now = _utc_now()
            local_path = str(local_download_path) if local_download_path else None
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO ar_drive_files (
                        drive_file_id,
                        drive_name,
                        drive_mime_type,
                        drive_modified_time,
                        drive_date_folder,
                        processo_pasta,
                        relative_path,
                        local_download_path,
                        first_seen_at,
                        last_seen_at,
                        last_downloaded_at,
                        last_status,
                        last_error_message
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        drive_name = VALUES(drive_name),
                        drive_mime_type = VALUES(drive_mime_type),
                        drive_modified_time = VALUES(drive_modified_time),
                        drive_date_folder = VALUES(drive_date_folder),
                        processo_pasta = VALUES(processo_pasta),
                        relative_path = VALUES(relative_path),
                        local_download_path = COALESCE(VALUES(local_download_path), local_download_path),
                        last_seen_at = VALUES(last_seen_at),
                        last_downloaded_at = COALESCE(VALUES(last_downloaded_at), last_downloaded_at),
                        last_status = VALUES(last_status),
                        last_error_message = VALUES(last_error_message)
                    """,
                    (
                        drive_file.file_id,
                        drive_file.name,
                        drive_file.mime_type,
                        _mysql_datetime(modified_time),
                        drive_date_folder,
                        _process_key(drive_file),
                        drive_file.relative_path or None,
                        local_path,
                        _mysql_datetime(now),
                        _mysql_datetime(now),
                        _mysql_datetime(now) if mark_downloaded else None,
                        last_status,
                        last_error_message,
                    ),
                )
                conn.commit()
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "operational_db_drive_file_upsert_failed",
                file_id=drive_file.file_id,
                status=last_status,
                error=str(exc),
            )

    def _ensure_processo(self, cursor: Any, registro: dict[str, Any]) -> int:
        demonstrativo = registro.get("demonstrativo", {})
        processo_pasta = demonstrativo.get("processo_pasta") or _first_record_part(
            registro.get("id"),
            0,
        )
        processo_pasta = str(processo_pasta or "sem_processo")
        cursor.execute(
            """
            INSERT INTO ar_processos (processo_pasta, status)
            VALUES (%s, 'processing')
            ON DUPLICATE KEY UPDATE
                status = IF(status = 'completed', status, 'processing')
            """,
            (processo_pasta,),
        )
        cursor.execute(
            "SELECT id FROM ar_processos WHERE processo_pasta = %s LIMIT 1",
            (processo_pasta,),
        )
        return int(cursor.fetchone()[0])

    def _ensure_requerente(
        self,
        cursor: Any,
        payload: dict[str, Any],
        registro: dict[str, Any],
    ) -> int | None:
        requerente_raw = payload.get("requerente") or {}
        if isinstance(requerente_raw, str):
            requerente = {"nome": requerente_raw}
        elif isinstance(requerente_raw, dict):
            requerente = requerente_raw
        else:
            requerente = {}
        demonstrativo = registro.get("demonstrativo", {})
        nome = requerente.get("nome") or demonstrativo.get("beneficiario_nome")
        cpf_cnpj = requerente.get("cpf_cnpj") or demonstrativo.get("beneficiario_cpf_cnpj")
        documento_digits = _digits_only(cpf_cnpj)
        requerente_key = (
            payload.get("requerente_key")
            or documento_digits
            or _safe_key(nome)
            or "requerente_sem_identificacao"
        )
        tipo_pessoa = _tipo_pessoa(documento_digits)
        cursor.execute(
            """
            INSERT INTO ar_requerentes (
                requerente_key, nome, cpf_cnpj, documento_digits, tipo_pessoa
            ) VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                nome = COALESCE(VALUES(nome), nome),
                cpf_cnpj = COALESCE(VALUES(cpf_cnpj), cpf_cnpj),
                documento_digits = COALESCE(VALUES(documento_digits), documento_digits),
                tipo_pessoa = VALUES(tipo_pessoa)
            """,
            (requerente_key, nome, cpf_cnpj, documento_digits or None, tipo_pessoa),
        )
        cursor.execute(
            "SELECT id FROM ar_requerentes WHERE requerente_key = %s LIMIT 1",
            (requerente_key,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None

    def _ensure_requisicao(
        self,
        cursor: Any,
        registro: dict[str, Any],
        *,
        processo_id: int,
        requerente_id: int | None,
        classificacao: str,
    ) -> int:
        demonstrativo = registro.get("demonstrativo", {})
        status = _requisicao_status(classificacao)
        banco = _normalize_banco(demonstrativo.get("banco"))
        cursor.execute(
            """
            INSERT INTO ar_requisicoes (
                processo_id, requerente_id, processo_requisicao, banco,
                agencia, conta_deposito, mes_pagamento,
                data_disponibilidade_saque, demonstrativo_json_path,
                demonstrativo_pdf_path, status, classificacao
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                requerente_id = VALUES(requerente_id),
                agencia = VALUES(agencia),
                mes_pagamento = VALUES(mes_pagamento),
                data_disponibilidade_saque = VALUES(data_disponibilidade_saque),
                demonstrativo_json_path = VALUES(demonstrativo_json_path),
                demonstrativo_pdf_path = VALUES(demonstrativo_pdf_path),
                status = VALUES(status),
                classificacao = VALUES(classificacao)
            """,
            (
                processo_id,
                requerente_id,
                demonstrativo.get("processo_requisicao")
                or _first_record_part(registro.get("id"), 1)
                or "sem_requisicao",
                banco,
                demonstrativo.get("agencia"),
                str(demonstrativo.get("conta_deposito") or "sem_conta"),
                demonstrativo.get("mes_pagamento"),
                _parse_date(demonstrativo.get("data_disponibilidade_saque")),
                demonstrativo.get("json_path"),
                demonstrativo.get("pdf_extraido_path"),
                status,
                classificacao,
            ),
        )
        cursor.execute(
            """
            SELECT id
            FROM ar_requisicoes
            WHERE processo_id = %s
              AND processo_requisicao = %s
              AND banco = %s
              AND conta_deposito = %s
            LIMIT 1
            """,
            (
                processo_id,
                demonstrativo.get("processo_requisicao")
                or _first_record_part(registro.get("id"), 1)
                or "sem_requisicao",
                banco,
                str(demonstrativo.get("conta_deposito") or "sem_conta"),
            ),
        )
        return int(cursor.fetchone()[0])

    def _upsert_artifact(
        self,
        cursor: Any,
        *,
        artifact_type: str,
        storage_path: str | Path | None,
        requisicao_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        if not storage_path:
            return None
        path = Path(str(storage_path))
        path_hash = storage_path_hash(path)
        exists = path.exists()
        cursor.execute(
            """
            INSERT INTO ar_artifacts (
                requisicao_id, artifact_type, storage_path, storage_path_hash,
                file_name, file_size_bytes, sha256, status, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                requisicao_id = COALESCE(VALUES(requisicao_id), requisicao_id),
                artifact_type = VALUES(artifact_type),
                file_name = VALUES(file_name),
                file_size_bytes = VALUES(file_size_bytes),
                sha256 = VALUES(sha256),
                status = VALUES(status),
                metadata_json = VALUES(metadata_json)
            """,
            (
                requisicao_id,
                artifact_type,
                str(path),
                path_hash,
                path.name,
                path.stat().st_size if exists else None,
                _sha256_file(path) if exists and path.is_file() else None,
                "created" if exists else "missing",
                _json_dumps(metadata or {}),
            ),
        )
        cursor.execute(
            "SELECT id FROM ar_artifacts WHERE storage_path_hash = %s LIMIT 1",
            (path_hash,),
        )
        return int(cursor.fetchone()[0])

    def _upsert_comprovante_coleta(
        self,
        cursor: Any,
        registro: dict[str, Any],
        requisicao_id: int,
    ) -> None:
        demonstrativo = registro.get("demonstrativo", {})
        comprovante = registro.get("comprovante", {})
        status = _coleta_status(registro)
        pdf_artifact_id = self._upsert_artifact(
            cursor,
            artifact_type="comprovante_pdf",
            storage_path=comprovante.get("pdf_path"),
            requisicao_id=requisicao_id,
        )
        cursor.execute(
            """
            INSERT INTO ar_comprovante_coletas (
                requisicao_id, banco, status, pdf_artifact_id, attempts,
                periodo_usado, periodo_estrategia, attempted_periods_json,
                account_json, error_message, error_class, raw_status_json, collected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                banco = VALUES(banco),
                status = VALUES(status),
                pdf_artifact_id = VALUES(pdf_artifact_id),
                attempts = VALUES(attempts),
                periodo_usado = VALUES(periodo_usado),
                periodo_estrategia = VALUES(periodo_estrategia),
                attempted_periods_json = VALUES(attempted_periods_json),
                account_json = VALUES(account_json),
                error_message = VALUES(error_message),
                error_class = VALUES(error_class),
                raw_status_json = VALUES(raw_status_json),
                collected_at = VALUES(collected_at)
            """,
            (
                requisicao_id,
                _normalize_banco(demonstrativo.get("banco"))
                if _normalize_banco(demonstrativo.get("banco")) in {"CEF", "BB"}
                else "CEF",
                status,
                pdf_artifact_id,
                int(comprovante.get("attempts") or 0),
                comprovante.get("periodo_usado"),
                comprovante.get("periodo_estrategia"),
                _json_dumps(
                    (comprovante.get("account") or {}).get("periodos")
                    or comprovante.get("attempted_periods")
                    or []
                ),
                _json_dumps(comprovante.get("account") or {}),
                comprovante.get("error"),
                comprovante.get("error_class"),
                _json_dumps(comprovante),
                _utc_mysql_now() if comprovante.get("success") else None,
            ),
        )

    def _upsert_ir_validacao(
        self,
        cursor: Any,
        registro: dict[str, Any],
        requisicao_id: int,
        *,
        classificacao: str,
        payload: dict[str, Any],
    ) -> None:
        comprovante = registro.get("comprovante", {})
        ir = comprovante.get("ir_retido", {})
        cursor.execute(
            """
            INSERT INTO ar_ir_validacoes (
                requisicao_id, tem_ir_retido, valor_ir_retido, campo_origem,
                deducao_previdenciaria, numero_meses_rra, data_levantamento,
                valor_bruto_resgate, valor_liquido_resgate, classificacao,
                motivo_inaptidao, validation_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                tem_ir_retido = VALUES(tem_ir_retido),
                valor_ir_retido = VALUES(valor_ir_retido),
                campo_origem = VALUES(campo_origem),
                deducao_previdenciaria = VALUES(deducao_previdenciaria),
                numero_meses_rra = VALUES(numero_meses_rra),
                data_levantamento = VALUES(data_levantamento),
                valor_bruto_resgate = VALUES(valor_bruto_resgate),
                valor_liquido_resgate = VALUES(valor_liquido_resgate),
                classificacao = VALUES(classificacao),
                motivo_inaptidao = VALUES(motivo_inaptidao),
                validation_json = VALUES(validation_json)
            """,
            (
                requisicao_id,
                bool(ir.get("tem_ir_retido")),
                _decimal_or_none(ir.get("valor_ir_retido")),
                ir.get("campo_origem"),
                _decimal_or_none(ir.get("deducao_previdenciaria")),
                _decimal_or_none(ir.get("numero_meses_rra")),
                _parse_date(comprovante.get("data_levantamento")),
                _decimal_or_none(comprovante.get("valor_bruto_resgate")),
                _decimal_or_none(comprovante.get("valor_liquido_resgate")),
                _validacao_classificacao(classificacao),
                payload.get("motivo_inaptidao"),
                _json_dumps({"status": registro.get("status"), "ir_retido": ir}),
            ),
        )

    def _upsert_calculo_ia(
        self,
        cursor: Any,
        output_payload: dict[str, Any],
        *,
        requisicao_id: int,
        artifact_id: int | None,
        openai_model: str,
    ) -> int:
        receita_rra = output_payload.get("resultado_receita_rra") or {}
        imposto_atualizado = (
            output_payload.get("resultado_imposto_retido_indevidamente_atualizado")
            or {}
        )
        status = "processado"
        if receita_rra.get("status") == "nao_calculado":
            status = "nao_calculado"
        elif receita_rra.get("status") == "erro" or imposto_atualizado.get("status") == "erro":
            status = "falha"
        cursor.execute(
            """
            INSERT INTO ar_calculos_ia (
                requisicao_id, attempt_number, status, tipo_fluxo, openai_model,
                prompt_path, arquivos_otimizados_para_openai,
                tamanho_total_arquivos_bytes, output_artifact_id, parsed_json,
                receita_rra_status, receita_rra_motivo,
                imposto_atualizado_status, imposto_atualizado_motivo,
                error_message, error_class, processed_at
            ) VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                tipo_fluxo = VALUES(tipo_fluxo),
                openai_model = VALUES(openai_model),
                prompt_path = VALUES(prompt_path),
                arquivos_otimizados_para_openai = VALUES(arquivos_otimizados_para_openai),
                tamanho_total_arquivos_bytes = VALUES(tamanho_total_arquivos_bytes),
                output_artifact_id = VALUES(output_artifact_id),
                parsed_json = VALUES(parsed_json),
                receita_rra_status = VALUES(receita_rra_status),
                receita_rra_motivo = VALUES(receita_rra_motivo),
                imposto_atualizado_status = VALUES(imposto_atualizado_status),
                imposto_atualizado_motivo = VALUES(imposto_atualizado_motivo),
                error_message = VALUES(error_message),
                error_class = VALUES(error_class),
                processed_at = VALUES(processed_at)
            """,
            (
                requisicao_id,
                status,
                (output_payload.get("decisao_fluxo") or {}).get("tipo_fluxo"),
                openai_model,
                (output_payload.get("decisao_fluxo") or {}).get("prompt_usado"),
                bool(output_payload.get("arquivos_otimizados_para_openai")),
                output_payload.get("tamanho_total_arquivos_bytes"),
                artifact_id,
                _json_dumps(output_payload.get("resultado") or {}),
                receita_rra.get("status"),
                receita_rra.get("motivo"),
                imposto_atualizado.get("status"),
                imposto_atualizado.get("motivo"),
                receita_rra.get("motivo") or imposto_atualizado.get("motivo"),
                None if status == "processado" else "unknown",
            ),
        )
        cursor.execute(
            """
            SELECT id
            FROM ar_calculos_ia
            WHERE requisicao_id = %s AND attempt_number = 1
            LIMIT 1
            """,
            (requisicao_id,),
        )
        return int(cursor.fetchone()[0])

    def _upsert_resultado_calculo(
        self,
        cursor: Any,
        output_payload: dict[str, Any],
        *,
        calculo_id: int,
        requisicao_id: int,
    ) -> None:
        receita_rra = output_payload.get("resultado_receita_rra") or {}
        rra_entrada = receita_rra.get("entrada") or {}
        rra_resultado = receita_rra.get("resultado") or {}
        imposto_atualizado = (
            output_payload.get("resultado_imposto_retido_indevidamente_atualizado")
            or {}
        )
        selic = imposto_atualizado.get("selic") or {}
        case = _first_case(output_payload.get("resultado") or {})
        alertas = []
        if case:
            alertas.extend(case.get("alertas") or [])
        alertas.extend((output_payload.get("resultado") or {}).get("alertas_gerais") or [])
        status_final = (
            "calculado"
            if receita_rra.get("status") == "calculado"
            and imposto_atualizado.get("status") == "calculado"
            else "nao_calculado"
        )
        mes_recebimento = rra_entrada.get("mes_recebimento") or rra_resultado.get(
            "mes_recebimento"
        )
        ano_recebimento = rra_entrada.get("ano_calendario") or rra_resultado.get(
            "ano_calendario"
        )
        cursor.execute(
            """
            INSERT INTO ar_resultados_calculo (
                calculo_ia_id, requisicao_id, status_final, ano_calendario,
                mes_recebimento, ano_recebimento, numero_meses_rra,
                rendimentos_recebidos, juros, previdencia_oficial,
                rendimentos_tributaveis, total_deducoes, base_calculo,
                aliquota_aplicada_percentual, parcela_deducao, irrf_calculado,
                imposto_retido, imposto_indevido, selic_vencimento,
                selic_pagamento, selic_percentual, selic_acumulado,
                imposto_retido_indevidamente_atualizado, fonte_tabela_receita,
                fonte_selic, alertas_json, resultado_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status_final = VALUES(status_final),
                ano_calendario = VALUES(ano_calendario),
                mes_recebimento = VALUES(mes_recebimento),
                ano_recebimento = VALUES(ano_recebimento),
                numero_meses_rra = VALUES(numero_meses_rra),
                rendimentos_recebidos = VALUES(rendimentos_recebidos),
                juros = VALUES(juros),
                previdencia_oficial = VALUES(previdencia_oficial),
                rendimentos_tributaveis = VALUES(rendimentos_tributaveis),
                total_deducoes = VALUES(total_deducoes),
                base_calculo = VALUES(base_calculo),
                aliquota_aplicada_percentual = VALUES(aliquota_aplicada_percentual),
                parcela_deducao = VALUES(parcela_deducao),
                irrf_calculado = VALUES(irrf_calculado),
                imposto_retido = VALUES(imposto_retido),
                imposto_indevido = VALUES(imposto_indevido),
                selic_vencimento = VALUES(selic_vencimento),
                selic_pagamento = VALUES(selic_pagamento),
                selic_percentual = VALUES(selic_percentual),
                selic_acumulado = VALUES(selic_acumulado),
                imposto_retido_indevidamente_atualizado = VALUES(imposto_retido_indevidamente_atualizado),
                fonte_tabela_receita = VALUES(fonte_tabela_receita),
                fonte_selic = VALUES(fonte_selic),
                alertas_json = VALUES(alertas_json),
                resultado_json = VALUES(resultado_json)
            """,
            (
                calculo_id,
                requisicao_id,
                status_final,
                rra_entrada.get("ano_calendario") or rra_resultado.get("ano_calendario"),
                mes_recebimento,
                ano_recebimento,
                _decimal_or_none(rra_entrada.get("numero_meses")),
                _decimal_or_none(rra_entrada.get("rendimentos_recebidos")),
                _decimal_or_none(rra_entrada.get("juros")),
                _decimal_or_none(rra_entrada.get("previdencia_oficial")),
                _decimal_or_none(rra_resultado.get("rendimentos_tributaveis")),
                _decimal_or_none(rra_resultado.get("total_deducoes")),
                _decimal_or_none(rra_resultado.get("base_calculo")),
                _decimal_or_none(rra_resultado.get("aliquota_aplicada_percentual")),
                _decimal_or_none(rra_resultado.get("parcela_deducao")),
                _decimal_or_none(rra_resultado.get("irrf")),
                _decimal_or_none(imposto_atualizado.get("imposto_retido")),
                _decimal_or_none(imposto_atualizado.get("imposto_indevido")),
                selic.get("vencimento"),
                selic.get("pagamento"),
                _decimal_or_none(selic.get("percentual")),
                _decimal_or_none(imposto_atualizado.get("selic_acumulado")),
                _decimal_or_none(
                    imposto_atualizado.get("imposto_retido_indevidamente_atualizado")
                ),
                rra_resultado.get("fonte_tabela"),
                selic.get("fonte"),
                _json_dumps(alertas),
                _json_dumps(output_payload),
            ),
        )

    def _update_processo_totals(self, cursor: Any, processo_id: int) -> None:
        cursor.execute(
            """
            UPDATE ar_processos p
            SET total_requisicoes = (
                    SELECT COUNT(*) FROM ar_requisicoes r WHERE r.processo_id = p.id
                ),
                total_requerentes = (
                    SELECT COUNT(DISTINCT requerente_id)
                    FROM ar_requisicoes r
                    WHERE r.processo_id = p.id AND r.requerente_id IS NOT NULL
                ),
                total_aptos = (
                    SELECT COUNT(*)
                    FROM ar_requisicoes r
                    WHERE r.processo_id = p.id AND r.classificacao = 'apto_com_ir'
                ),
                total_inaptos = (
                    SELECT COUNT(*)
                    FROM ar_requisicoes r
                    WHERE r.processo_id = p.id
                      AND r.classificacao <> 'apto_com_ir'
                )
            WHERE p.id = %s
            """,
            (processo_id,),
        )

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        try:
            import mysql.connector
        except ImportError as exc:
            raise RuntimeError(
                "Dependência mysql-connector-python não instalada. "
                "Execute: pip install -r requirements.txt"
            ) from exc

        conn = mysql.connector.connect(
            host=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.user,
            password=self._config.password,
            autocommit=False,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            connection_timeout=15,
        )
        try:
            yield conn
        finally:
            conn.close()


    def list_failed_comprovante_coletas(
        self,
        *,
        bancos: list[str] | None = None,
        error_classes: list[str] | None = None,
        statuses: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista coletas de comprovante com falha/não localizado para reprocessamento."""
        status_filter = statuses or ["nao_localizado", "falha"]
        banco_filter = [b.upper() for b in (bancos or []) if b and b.upper() != "ALL"]
        error_filter = [e.strip().lower() for e in (error_classes or []) if e and e.strip()]

        clauses = ["cc.status IN ({})".format(", ".join(["%s"] * len(status_filter)))]
        params: list[Any] = list(status_filter)

        if banco_filter:
            clauses.append(
                "cc.banco IN ({})".format(", ".join(["%s"] * len(banco_filter)))
            )
            params.extend(banco_filter)

        if error_filter:
            clauses.append(
                "LOWER(COALESCE(cc.error_class, '')) IN ({})".format(
                    ", ".join(["%s"] * len(error_filter))
                )
            )
            params.extend(error_filter)

        sql = f"""
            SELECT
                cc.id AS coleta_id,
                cc.banco,
                cc.status,
                cc.error_class,
                cc.error_message,
                cc.updated_at,
                cc.account_json,
                req.id AS requisicao_id,
                req.processo_requisicao,
                req.agencia,
                req.conta_deposito,
                req.mes_pagamento,
                req.data_disponibilidade_saque,
                req.demonstrativo_json_path,
                p.processo_pasta,
                r.nome AS requerente_nome,
                r.cpf_cnpj AS requerente_cpf_cnpj
            FROM ar_comprovante_coletas cc
            INNER JOIN ar_requisicoes req ON req.id = cc.requisicao_id
            INNER JOIN ar_processos p ON p.id = req.processo_id
            LEFT JOIN ar_requerentes r ON r.id = req.requerente_id
            WHERE {" AND ".join(clauses)}
            ORDER BY cc.updated_at DESC
        """
        if limit is not None and limit > 0:
            sql += " LIMIT %s"
            params.append(int(limit))

        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, tuple(params))
            rows = list(cursor.fetchall() or [])
            cursor.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            account_json = row.get("account_json")
            if isinstance(account_json, str):
                try:
                    account_json = json.loads(account_json)
                except Exception:  # noqa: BLE001
                    account_json = None
            result.append(
                {
                    "coleta_id": row.get("coleta_id"),
                    "requisicao_id": row.get("requisicao_id"),
                    "banco": str(row.get("banco") or "").upper(),
                    "status": row.get("status"),
                    "error_class": row.get("error_class"),
                    "error_message": row.get("error_message"),
                    "updated_at": row.get("updated_at"),
                    "processo_pasta": row.get("processo_pasta"),
                    "processo_requisicao": row.get("processo_requisicao"),
                    "agencia": row.get("agencia"),
                    "conta_deposito": row.get("conta_deposito"),
                    "mes_pagamento": row.get("mes_pagamento"),
                    "data_disponibilidade_saque": row.get("data_disponibilidade_saque"),
                    "demonstrativo_json_path": row.get("demonstrativo_json_path"),
                    "requerente_nome": row.get("requerente_nome"),
                    "requerente_cpf_cnpj": row.get("requerente_cpf_cnpj"),
                    "account_json": account_json if isinstance(account_json, dict) else None,
                    "record_key": comprovante_record_key(
                        processo_requisicao=row.get("processo_requisicao"),
                        conta_deposito=row.get("conta_deposito"),
                        banco=row.get("banco"),
                    ),
                }
            )
        return result

    def list_processes_with_cpf_absent_failures(
        self,
        *,
        bancos: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista processos com falha 'CPF ausente ou inválido' (únicos por pasta)."""
        banco_filter = [b.upper() for b in (bancos or []) if b and b.upper() != "ALL"]
        clauses = [
            "cc.status = 'falha'",
            "cc.error_message LIKE %s",
        ]
        params: list[Any] = ["%CPF ausente%"]
        if banco_filter:
            clauses.append(
                "cc.banco IN ({})".format(", ".join(["%s"] * len(banco_filter)))
            )
            params.extend(banco_filter)

        sql = f"""
            SELECT
                p.processo_pasta,
                p.drive_date_folder,
                cc.banco,
                COUNT(*) AS falhas,
                MAX(cc.updated_at) AS last_updated_at,
                MAX(req.demonstrativo_json_path) AS demonstrativo_json_path
            FROM ar_comprovante_coletas cc
            INNER JOIN ar_requisicoes req ON req.id = cc.requisicao_id
            INNER JOIN ar_processos p ON p.id = req.processo_id
            WHERE {" AND ".join(clauses)}
            GROUP BY p.processo_pasta, p.drive_date_folder, cc.banco
            ORDER BY last_updated_at DESC, p.processo_pasta ASC
        """
        if limit is not None and limit > 0:
            sql += " LIMIT %s"
            params.append(int(limit))

        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, tuple(params))
            rows = list(cursor.fetchall() or [])
            cursor.close()

        return [
            {
                "processo_pasta": row.get("processo_pasta"),
                "drive_date_folder": row.get("drive_date_folder") or None,
                "banco": str(row.get("banco") or "").upper(),
                "falhas": int(row.get("falhas") or 0),
                "last_updated_at": row.get("last_updated_at"),
                "demonstrativo_json_path": row.get("demonstrativo_json_path"),
            }
            for row in rows
            if row.get("processo_pasta")
        ]

    @staticmethod
    def demonstrativo_from_falha(item: dict[str, Any]) -> dict[str, Any]:
        """Monta um demonstrativo mínimo a partir da linha MySQL (sem JSON local)."""
        conta = item.get("conta_deposito")
        account = item.get("account_json") or {}
        if isinstance(account, dict) and account.get("conta_deposito_original"):
            conta = account.get("conta_deposito_original") or conta
        data_disp = item.get("data_disponibilidade_saque")
        if hasattr(data_disp, "strftime"):
            data_disp = data_disp.strftime("%d/%m/%Y")
        return {
            "banco": str(item.get("banco") or "").upper(),
            "agencia": item.get("agencia") or (account.get("agencia") if isinstance(account, dict) else None),
            "conta_deposito": conta,
            "processo_pasta": item.get("processo_pasta"),
            "processo_requisicao": item.get("processo_requisicao"),
            "beneficiario_nome": item.get("requerente_nome"),
            "beneficiario_cpf_cnpj": item.get("requerente_cpf_cnpj"),
            "mes_pagamento": item.get("mes_pagamento"),
            "data_disponibilidade_saque": data_disp,
            "arquivo": f"reprocess_db_{item.get('coleta_id')}",
            "fonte": "mysql_reprocess",
        }


def comprovante_record_key(
    *,
    processo_requisicao: Any,
    conta_deposito: Any,
    banco: Any,
) -> str:
    """Chave estável para casar falha MySQL ↔ JSON local de demonstrativo."""
    conta = re.sub(r"\D+", "", str(conta_deposito or "")) or str(conta_deposito or "").strip()
    return (
        f"{str(processo_requisicao or '').strip()}|"
        f"{conta}|"
        f"{str(banco or '').strip().upper()}"
    )


def storage_path_hash(path: str | Path) -> str:
    return hashlib.sha256(str(path).replace("\\", "/").encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _digits_only(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _tipo_pessoa(documento_digits: str) -> str:
    if len(documento_digits) == 11:
        return "PF"
    if len(documento_digits) == 14:
        return "PJ"
    return "INDETERMINADO"


def _safe_key(value: Any) -> str | None:
    if not value:
        return None
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_").lower() or None


def _first_record_part(record_id: Any, index: int) -> str | None:
    parts = str(record_id or "").split("|")
    if len(parts) <= index:
        return None
    return parts[index] or None


def _requisicao_identity(registro: dict[str, Any]) -> dict[str, str]:
    demonstrativo = registro.get("demonstrativo", {}) or {}
    processo_pasta = str(
        demonstrativo.get("processo_pasta")
        or _first_record_part(registro.get("id"), 0)
        or "sem_processo"
    )
    processo_requisicao = str(
        demonstrativo.get("processo_requisicao")
        or _first_record_part(registro.get("id"), 1)
        or "sem_requisicao"
    )
    banco = _normalize_banco(demonstrativo.get("banco"))
    conta_deposito = str(demonstrativo.get("conta_deposito") or "sem_conta")
    return {
        "processo_pasta": processo_pasta,
        "processo_requisicao": processo_requisicao,
        "banco": banco,
        "conta_deposito": conta_deposito,
    }


def _normalize_banco(value: Any) -> str:
    banco = str(value or "").upper()
    if banco in {"CEF", "BB"}:
        return banco
    return "OUTRO" if banco else "INDETERMINADO"


def _requisicao_status(classificacao: str) -> str:
    if classificacao == "apto_com_ir":
        return "apto"
    if classificacao == "ignorado_cnpj":
        return "ignorado"
    if classificacao == "inapto_falha_comprovante":
        return "falha"
    if classificacao.startswith("inapto_"):
        return "inapto"
    return "novo"


def _validacao_classificacao(classificacao: str) -> str:
    valid = {
        "apto_com_ir",
        "inapto_sem_ir",
        "inapto_sem_numero_meses",
        "inapto_falha_comprovante",
        "ignorado_cnpj",
    }
    return classificacao if classificacao in valid else "nao_classificado"


def _coleta_status(registro: dict[str, Any]) -> str:
    status = registro.get("status", {})
    comprovante = registro.get("comprovante", {})
    if (
        status.get("motivo_ignorado") == "cnpj_ignorado"
        or comprovante.get("reason") == "cnpj_ignorado"
    ):
        return "ignorado_cnpj"
    if comprovante.get("success") and not comprovante.get("skipped"):
        return "coletado"
    error_class = str(comprovante.get("error_class") or "").lower()
    if error_class == "not_found" or comprovante.get("reason") in {
        "not_found",
        "dados_nao_localizados",
    }:
        return "nao_localizado"
    if error_class == "skipped" or comprovante.get("skipped"):
        return "pulado"
    if comprovante.get("error") or error_class in {
        "proxy_error",
        "timeout",
        "connection_error",
        "portal_changed",
        "validation_error",
        "http_error",
        "unknown",
        "falha",
    }:
        return "falha"
    return "pulado"


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for pattern, order in (
        (r"^(\d{2})/(\d{2})/(\d{4})$", "dmy"),
        (r"^(\d{4})-(\d{2})-(\d{2})$", "ymd"),
    ):
        match = re.match(pattern, text)
        if not match:
            continue
        parts = [int(part) for part in match.groups()]
        if order == "dmy":
            return date(parts[2], parts[1], parts[0])
        return date(parts[0], parts[1], parts[2])
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_case(parsed_data: dict[str, Any]) -> dict[str, Any]:
    cases = parsed_data.get("casos") or []
    return cases[0] if cases else {}


def _utc_mysql_now() -> str:
    return _mysql_datetime(_utc_now())


def _parse_drive_datetime(value: str | None) -> datetime:
    if not value:
        return _utc_now()
    text = str(value).strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _mysql_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _same_datetime(left: datetime, right: Any) -> bool:
    if isinstance(right, datetime):
        right_dt = right
        if right_dt.tzinfo is None:
            right_dt = right_dt.replace(tzinfo=timezone.utc)
        return left.astimezone(timezone.utc) == right_dt.astimezone(timezone.utc)
    return _mysql_datetime(left) == str(right)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _process_key(drive_file: DriveFile) -> str:
    if drive_file.relative_path:
        return drive_file.relative_path.replace("\\", "/").split("/")[-1]
    return Path(drive_file.name).stem


def _split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    previous = ""

    for char in sql:
        if char == "'" and not in_double_quote and previous != "\\":
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote and previous != "\\":
            in_double_quote = not in_double_quote

        if char == ";" and not in_single_quote and not in_double_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
        previous = char

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements
