from __future__ import annotations

from typing import Protocol

from recupere_monday.config import Settings
from recupere_monday.gemini.processor import GeminiProcessor
from recupere_monday.openai.processor import ExtractionResult, OpenAIProcessor


class PdfExtractor(Protocol):
    def extract_from_pdf(self, pdf_path) -> ExtractionResult: ...

    def extract_from_pdfs(self, pdf_paths: list) -> ExtractionResult: ...


def build_pdf_extractor(
    settings: Settings,
    extraction_prompt: str,
) -> PdfExtractor:
    """Monta o processador de PDF conforme LLM_PROVIDER (openai | gemini)."""
    provider = settings.llm_provider.strip().lower()
    if provider == "gemini":
        return GeminiProcessor(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            extraction_prompt=extraction_prompt,
            max_retries=settings.max_retries,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if provider == "openai":
        return OpenAIProcessor(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            extraction_prompt=extraction_prompt,
            max_retries=settings.max_retries,
            timeout_seconds=settings.request_timeout_seconds,
        )
    raise ValueError(
        f"LLM_PROVIDER inválido: {settings.llm_provider!r}. Use 'openai' ou 'gemini'."
    )
