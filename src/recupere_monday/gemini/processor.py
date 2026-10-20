from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from recupere_monday.openai.processor import ExtractionResult, OpenAIQuotaError, parse_llm_json

logger = structlog.get_logger(__name__)

# Alias semântico: cota esgotada em qualquer provedor LLM.
GeminiQuotaError = OpenAIQuotaError


class GeminiProcessor:
    """Extrai JSON estruturado de PDF(s) via Gemini (mesmo contrato do OpenAIProcessor)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        extraction_prompt: str,
        max_retries: int = 3,
        timeout_seconds: int = 120,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )
        self._model = model
        self._prompt = extraction_prompt
        self._max_retries = max_retries

    def extract_from_pdf(self, pdf_path: Path) -> ExtractionResult:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF não encontrado: {pdf_path}")
        return self._call_gemini([pdf_path])

    def extract_from_pdfs(self, pdf_paths: list[Path]) -> ExtractionResult:
        if not pdf_paths:
            raise ValueError("nenhum PDF informado para a chamada Gemini")
        missing = [str(path) for path in pdf_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"PDF(s) não encontrado(s): {missing}")
        return self._call_gemini(pdf_paths)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    def _call_gemini(self, pdf_paths: list[Path]) -> ExtractionResult:
        uploaded_names: list[str] = []
        try:
            uploaded_files = []
            for pdf_path in pdf_paths:
                uploaded = self._client.files.upload(file=str(pdf_path))
                uploaded = self._wait_file_active(uploaded)
                uploaded_names.append(uploaded.name)
                uploaded_files.append(uploaded)

            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=[self._prompt, *uploaded_files],
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                if _is_quota_exhausted(exc):
                    raise GeminiQuotaError(str(exc)) from exc
                raise

            raw = (response.text or "").strip() or "{}"
            parsed = self._parse_json(raw)
            logger.info(
                "gemini_extraction_complete",
                files=[path.name for path in pdf_paths],
                fields=list(parsed.keys()),
                model=self._model,
            )
            return ExtractionResult(
                raw_response=raw,
                parsed_data=parsed,
                model=self._model,
                openai_file_id=uploaded_names[0] if uploaded_names else None,
                openai_file_ids=uploaded_names or None,
            )
        finally:
            for name in uploaded_names:
                self._safe_delete_file(name)

    def _wait_file_active(self, uploaded: Any, *, timeout_seconds: int = 120) -> Any:
        deadline = time.monotonic() + timeout_seconds
        current = uploaded
        while True:
            state = getattr(getattr(current, "state", None), "name", None) or str(
                getattr(current, "state", "")
            )
            if state in {"ACTIVE", "FileState.ACTIVE", ""}:
                return current
            if state in {"FAILED", "FileState.FAILED"}:
                raise RuntimeError(f"upload Gemini falhou: {current.name} state={state}")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timeout aguardando arquivo Gemini ACTIVE: {current.name}"
                )
            time.sleep(1)
            current = self._client.files.get(name=current.name)

    def _safe_delete_file(self, file_name: str) -> None:
        try:
            self._client.files.delete(name=file_name)
        except Exception as exc:  # noqa: BLE001 — limpeza best-effort
            logger.warning("gemini_file_delete_failed", file_name=file_name, error=str(exc))

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        return parse_llm_json(raw)


def _is_quota_exhausted(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "resource_exhausted",
        "quota",
        "rate limit",
        "429",
        "insufficient",
    )
    return any(marker in text for marker in markers)
