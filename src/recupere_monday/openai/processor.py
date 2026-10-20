from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ExtractionResult:
    raw_response: str
    parsed_data: dict[str, Any]
    model: str
    openai_file_id: str | None
    openai_file_ids: list[str] | None = None


class OpenAIQuotaError(RuntimeError):
    """Cota da API OpenAI esgotada — não adianta retentar."""


class OpenAIProcessor:
    def __init__(
        self,
        api_key: str,
        model: str,
        extraction_prompt: str,
        max_retries: int = 3,
        timeout_seconds: int = 120,
    ) -> None:
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._prompt = extraction_prompt
        self._max_retries = max_retries

    def extract_from_pdf(self, pdf_path: Path) -> ExtractionResult:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF não encontrado: {pdf_path}")
        return self._extract_with_retry(pdf_path)

    def extract_from_pdfs(self, pdf_paths: list[Path]) -> ExtractionResult:
        if not pdf_paths:
            raise ValueError("nenhum PDF informado para a chamada OpenAI")
        missing = [str(path) for path in pdf_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"PDF(s) não encontrado(s): {missing}")
        return self._call_openai_many(pdf_paths)

    def _extract_with_retry(self, pdf_path: Path) -> ExtractionResult:
        return self._call_openai(pdf_path)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    def _call_openai(self, pdf_path: Path) -> ExtractionResult:
        uploaded_file_id: str | None = None
        try:
            with pdf_path.open("rb") as file_handle:
                uploaded = self._client.files.create(
                    file=file_handle,
                    purpose="user_data",
                )
            uploaded_file_id = uploaded.id

            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": self._prompt},
                                {
                                    "type": "file",
                                    "file": {"file_id": uploaded_file_id},
                                },
                            ],
                        }
                    ],
                    response_format={"type": "json_object"},
                    temperature=0,
                )
            except RateLimitError as exc:
                if _is_insufficient_quota(exc):
                    raise OpenAIQuotaError(str(exc)) from exc
                raise

            raw = response.choices[0].message.content or "{}"
            parsed = self._parse_json(raw)

            logger.info(
                "openai_extraction_complete",
                file=pdf_path.name,
                fields=list(parsed.keys()),
            )

            return ExtractionResult(
                raw_response=raw,
                parsed_data=parsed,
                model=self._model,
                openai_file_id=uploaded_file_id,
                openai_file_ids=[uploaded_file_id] if uploaded_file_id else None,
            )
        finally:
            if uploaded_file_id:
                self._safe_delete_file(uploaded_file_id)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    def _call_openai_many(self, pdf_paths: list[Path]) -> ExtractionResult:
        uploaded_file_ids: list[str] = []
        try:
            for pdf_path in pdf_paths:
                with pdf_path.open("rb") as file_handle:
                    uploaded = self._client.files.create(
                        file=file_handle,
                        purpose="user_data",
                    )
                uploaded_file_ids.append(uploaded.id)

            content: list[dict[str, Any]] = [{"type": "text", "text": self._prompt}]
            content.extend(
                {"type": "file", "file": {"file_id": file_id}}
                for file_id in uploaded_file_ids
            )

            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": content}],
                    response_format={"type": "json_object"},
                    temperature=0,
                )
            except RateLimitError as exc:
                if _is_insufficient_quota(exc):
                    raise OpenAIQuotaError(str(exc)) from exc
                raise

            raw = response.choices[0].message.content or "{}"
            parsed = self._parse_json(raw)
            logger.info(
                "openai_multi_pdf_extraction_complete",
                files=[path.name for path in pdf_paths],
                fields=list(parsed.keys()),
            )
            return ExtractionResult(
                raw_response=raw,
                parsed_data=parsed,
                model=self._model,
                openai_file_id=uploaded_file_ids[0] if uploaded_file_ids else None,
                openai_file_ids=uploaded_file_ids,
            )
        finally:
            for file_id in uploaded_file_ids:
                self._safe_delete_file(file_id)

    def _safe_delete_file(self, file_id: str) -> None:
        try:
            self._client.files.delete(file_id)
        except Exception as exc:  # noqa: BLE001 — limpeza best-effort
            logger.warning("openai_file_delete_failed", file_id=file_id, error=str(exc))

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        return parse_llm_json(raw)


def parse_llm_json(raw: str) -> dict[str, Any]:
    """Parse JSON da LLM; tenta recuperar respostas truncadas ou com fences."""
    text = (raw or "").strip()
    if not text:
        return {"conteudo_bruto": raw}

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    for candidate in (text, _repair_truncated_json(text)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    return {"conteudo_bruto": raw}


def _repair_truncated_json(text: str) -> str | None:
    """Fecha aspas/chaves/colchetes abertos em JSON truncado pela LLM."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()

    repaired = text
    if in_str:
        repaired += '"'
    repaired = repaired.rstrip().rstrip(",")
    if not stack:
        return repaired if repaired != text else None
    return repaired + "".join(reversed(stack))


def _is_insufficient_quota(exc: RateLimitError) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        code = body.get("error", {}).get("code")
        return code == "insufficient_quota"
    return "insufficient_quota" in str(exc)
