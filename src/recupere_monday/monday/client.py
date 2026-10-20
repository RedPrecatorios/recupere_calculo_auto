from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import structlog

logger = structlog.get_logger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_FILE_API_URL = "https://api.monday.com/v2/file"


class MondayClient:
    def __init__(
        self,
        api_token: str,
        board_id: int,
        group_id: str = "topics",
        timeout_seconds: int = 120,
    ) -> None:
        self._api_token = api_token
        self._board_id = board_id
        self._group_id = group_id
        self._timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=MONDAY_API_URL,
            headers={
                "Authorization": api_token,
                "Content-Type": "application/json",
                "API-Version": "2024-10",
            },
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MondayClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def create_item(
        self,
        item_name: str,
        column_values: dict[str, Any] | None = None,
    ) -> int:
        column_values = column_values or {}
        group_id = self._resolve_group_id()
        column_values = self._cast_column_values_for_board(column_values)
        mutation = """
        mutation ($boardId: ID!, $groupId: String!, $itemName: String!, $columnValues: JSON) {
          create_item(
            board_id: $boardId
            group_id: $groupId
            item_name: $itemName
            column_values: $columnValues
          ) {
            id
          }
        }
        """
        variables = {
            "boardId": str(self._board_id),
            "groupId": group_id,
            "itemName": item_name,
            "columnValues": json.dumps(column_values, ensure_ascii=False),
        }

        data = self._execute(mutation, variables)
        item_id = int(data["create_item"]["id"])
        logger.info("monday_item_created", item_id=item_id, item_name=item_name)
        return item_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def find_items_by_column_values(
        self,
        columns: dict[str, Any],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Busca itens do board que casem com todos os valores de coluna informados."""
        filters = [
            {"column_id": str(column_id), "column_values": [str(value).strip()]}
            for column_id, value in (columns or {}).items()
            if str(value or "").strip()
        ]
        if not filters:
            return []

        query = """
        query ($boardId: ID!, $columns: [ItemsPageByColumnValuesQuery!], $limit: Int!) {
          items_page_by_column_values(
            board_id: $boardId
            columns: $columns
            limit: $limit
          ) {
            items { id name }
          }
        }
        """
        data = self._execute(
            query,
            {
                "boardId": str(self._board_id),
                "columns": filters,
                "limit": limit,
            },
        )
        page = data.get("items_page_by_column_values") or {}
        return [dict(item) for item in (page.get("items") or [])]

    def find_existing_item(self, columns: dict[str, Any]) -> dict[str, Any] | None:
        items = self.find_items_by_column_values(columns, limit=1)
        return items[0] if items else None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def add_file_to_column(
        self,
        item_id: int,
        column_id: str,
        file_path: Path,
    ) -> str:
        """Anexa um arquivo à coluna Arquivos do item (aparece na aba Arquivos)."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo para Monday não encontrado: {path}")
        column_id = str(column_id or "").strip()
        if not column_id:
            raise ValueError("MONDAY_FILES_COLUMN_ID vazio")

        query = (
            "mutation ($file: File!) {"
            f" add_file_to_column (file: $file, item_id: {int(item_id)},"
            f' column_id: "{column_id}") {{ id }}'
            " }"
        )
        with path.open("rb") as handle:
            response = httpx.post(
                MONDAY_FILE_API_URL,
                headers={
                    "Authorization": self._api_token,
                    "API-Version": "2024-10",
                },
                files={
                    "query": (None, query),
                    "map": (None, '{"file":"variables.file"}'),
                    "file": (path.name, handle, "application/pdf"),
                },
                timeout=self._timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json()
        if "errors" in payload:
            raise RuntimeError(f"Monday file upload error: {payload['errors']}")
        asset_id = str(payload["data"]["add_file_to_column"]["id"])
        logger.info(
            "monday_file_uploaded_to_column",
            item_id=item_id,
            column_id=column_id,
            file=path.name,
            asset_id=asset_id,
        )
        return asset_id

    def find_files_column_id(self) -> str | None:
        for column in self.list_columns():
            col_type = str(column.get("type") or "").casefold()
            if col_type in {"file", "files"}:
                return str(column.get("id"))
        return None

    def ensure_files_column_id(
        self,
        preferred_id: str = "",
        *,
        title: str = "Arquivos IR",
    ) -> str:
        preferred = str(preferred_id or "").strip()
        columns = self.list_columns()
        col_ids = {str(col.get("id")) for col in columns}
        if preferred and preferred in col_ids:
            return preferred
        detected = self.find_files_column_id()
        if detected:
            return detected
        return self.create_files_column(title=title)

    def create_files_column(self, *, title: str = "Arquivos IR") -> str:
        mutation = """
        mutation ($boardId: ID!, $title: String!) {
          create_column(board_id: $boardId, title: $title, column_type: file) {
            id
            title
            type
          }
        }
        """
        data = self._execute(
            mutation,
            {"boardId": str(self._board_id), "title": title},
        )
        column_id = str(data["create_column"]["id"])
        logger.info(
            "monday_files_column_created",
            column_id=column_id,
            title=title,
        )
        return column_id

    def attach_pdfs_to_files_column(
        self,
        item_id: int,
        pdf_paths: list[Path],
        *,
        column_id: str = "",
    ) -> list[dict[str, str]]:
        """Anexa PDFs na coluna Arquivos (sem criar Atualização)."""
        resolved_column = self.ensure_files_column_id(column_id)
        uploaded: list[dict[str, str]] = []
        for pdf_path in pdf_paths:
            if not pdf_path:
                continue
            path = Path(pdf_path)
            if not path.exists():
                logger.warning(
                    "monday_file_skip_missing",
                    item_id=item_id,
                    file=str(path),
                )
                continue
            asset_id = self.add_file_to_column(item_id, resolved_column, path)
            uploaded.append(
                {
                    "path": str(path),
                    "asset_id": asset_id,
                    "name": path.name,
                    "column_id": resolved_column,
                }
            )
        return uploaded

    def _resolve_group_id(self) -> str:
        groups = self.list_groups()
        configured = str(self._group_id or "").strip()
        if not configured:
            raise RuntimeError("MONDAY_GROUP_ID vazio")

        for group in groups:
            if group.get("id") == configured:
                return configured

        configured_normalized = _normalize(configured)
        for group in groups:
            if _normalize(str(group.get("title") or "")) == configured_normalized:
                group_id = str(group["id"])
                logger.info(
                    "monday_group_resolved_by_title",
                    configured=configured,
                    group_id=group_id,
                    group_title=group.get("title"),
                )
                return group_id

        available = ", ".join(
            f"{group.get('id')} ({group.get('title')})" for group in groups
        )
        raise RuntimeError(
            f"Grupo Monday não encontrado: {configured}. Grupos disponíveis: {available}"
        )

    def list_groups(self) -> list[dict[str, Any]]:
        query = """
        query ($boardId: [ID!]) {
          boards(ids: $boardId) {
            groups { id title }
          }
        }
        """
        data = self._execute(query, {"boardId": [str(self._board_id)]})
        boards = data.get("boards") or []
        if not boards:
            raise RuntimeError(f"Board Monday não encontrado: {self._board_id}")
        return list(boards[0].get("groups") or [])

    def list_columns(self) -> list[dict[str, Any]]:
        query = """
        query ($boardId: [ID!]) {
          boards(ids: $boardId) {
            columns { id title type }
          }
        }
        """
        data = self._execute(query, {"boardId": [str(self._board_id)]})
        boards = data.get("boards") or []
        if not boards:
            raise RuntimeError(f"Board Monday não encontrado: {self._board_id}")
        return list(boards[0].get("columns") or [])

    def _cast_column_values_for_board(
        self,
        column_values: dict[str, Any],
    ) -> dict[str, Any]:
        if not column_values:
            return {}
        column_types = {
            str(column.get("id")): str(column.get("type") or "")
            for column in self.list_columns()
        }
        return {
            column_id: _cast_column_value(column_types.get(column_id), value)
            for column_id, value in column_values.items()
        }

    def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(
            "",
            json={"query": query, "variables": variables},
        )
        response.raise_for_status()
        payload = response.json()

        if "errors" in payload:
            errors = payload["errors"]
            raise RuntimeError(f"Monday API error: {errors}")

        return payload["data"]

    @staticmethod
    def build_column_values(
        extracted_data: dict[str, Any],
        mapping: dict[str, str],
    ) -> dict[str, Any]:
        """
        Converte dados extraídos em column_values do Monday.

        mapping: {campo_json: coluna_monday}
        """
        if not mapping:
            return {}

        column_values: dict[str, Any] = {}
        for json_field, monday_column in mapping.items():
            if json_field not in extracted_data:
                continue
            value = extracted_data[json_field]
            if value is None or value == "":
                continue
            column_values[monday_column] = str(value)

        return column_values

    @staticmethod
    def resolve_item_name(
        file_name: str,
        extracted_data: dict[str, Any],
        preferred_fields: tuple[str, ...] = (
            "cliente",
            "nome",
            "titulo",
            "numero_processo",
            "processo",
        ),
    ) -> str:
        for field in preferred_fields:
            value = extracted_data.get(field)
            if value:
                return str(value)
        return _filename_stem(file_name)


def _filename_stem(name: str) -> str:
    return Path(name).stem


def _normalize(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _cast_column_value(column_type: str | None, value: Any) -> Any:
    if column_type == "status":
        return {"label": str(value)}
    if column_type == "date":
        iso_date = _to_iso_date(value)
        return {"date": iso_date} if iso_date else str(value)
    if column_type == "numbers":
        number = _to_decimal(value)
        return str(number) if number is not None else str(value)
    return str(value)


def _to_iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if iso_match:
        return iso_match.group(0)
    br_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if br_match:
        day, month, year = br_match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None
