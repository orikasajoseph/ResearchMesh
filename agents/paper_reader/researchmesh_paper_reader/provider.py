from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import httpx

from .config import PaperReaderConfig
from .schemas import PaperRef


ANALYSIS_QUESTION = """
Analyze this paper once using only the supplied paper evidence. Return exactly four
sections with these English labels, and cite the supporting PaperQA contexts in every
section:

RESEARCH_PROBLEM: the problem or research question addressed by the paper.
METHOD: the central method, approach, or viewpoint.
FINDINGS: the main findings, conclusions, or contributions.
LIMITATIONS: limitations, threats, open questions, or challenges stated by the authors.

Do not add unsupported background knowledge. Do not omit any of the four sections.
""".strip()

PQAC_PATTERN = re.compile(r"\bpqac-[0-9a-f]{8}\b", re.IGNORECASE)


class FullTextUnavailableError(RuntimeError):
    pass


class StructuredAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderContext:
    context_id: str
    locator: str
    raw_text: str
    summary: str
    score: int | float
    source: str


@dataclass(frozen=True)
class ProviderAnalysis:
    answer: str
    contexts: list[ProviderContext]
    references: str
    timings: dict[str, float]
    diagnostics: dict[str, Any] = field(default_factory=dict)


class PaperAnalysisProvider(Protocol):
    async def analyze(self, paper: PaperRef) -> ProviderAnalysis: ...


PdfFetcher = Callable[[str], Awaitable[bytes]]


class PaperQAProvider:
    """Long-lived PaperQA provider with one in-memory embedding model instance."""

    @staticmethod
    def router_config(config: PaperReaderConfig) -> dict[str, Any]:
        """Build the shared generation/summary Router with retries disabled."""
        alias = "researchmesh-deepseek"
        return {
            "model_list": [
                {
                    "model_name": alias,
                    "litellm_params": {
                        "model": config.provider_model,
                        "api_base": config.api_base,
                        "api_key": config.api_key,
                        "temperature": 0,
                        # LMI converts this legacy field to ModelSpec.max_retries.
                        "max_retries": 0,
                    },
                }
            ],
            # LiteLLM Router also receives an explicit zero so neither layer
            # silently restores its installed default retry policy.
            "router_kwargs": {"num_retries": 0},
        }

    def __init__(
        self,
        config: PaperReaderConfig | None = None,
        *,
        pdf_fetcher: PdfFetcher | None = None,
        initialization_audit=None,
    ) -> None:
        self.config = config or PaperReaderConfig.from_env()
        self._pdf_fetcher = pdf_fetcher

        def stage(name, action):
            if initialization_audit:
                initialization_audit(name, "started")
            try:
                value = action()
            except BaseException as error:
                if initialization_audit:
                    initialization_audit(name, "failed", type(error).__name__)
                raise
            if initialization_audit:
                initialization_audit(name, "completed")
            return value

        # Keep PaperQA optional for unit tests that inject a fake provider.
        def import_dependencies():
            from paperqa import Docs, Settings
            return Docs, Settings
        self._docs_type, settings_type = stage("paperqa_dependencies_import", import_dependencies)

        alias = "researchmesh-deepseek"
        router_config = self.router_config(self.config)
        self.settings = stage("paperqa_settings_construct", lambda: settings_type(
            llm=alias, summary_llm=alias, llm_config=router_config,
            summary_llm_config=router_config, embedding=self.config.embedding))
        self.settings.parsing.multimodal = False
        self.settings.parsing.use_doc_details = False
        self.settings.parsing.defer_embedding = False
        self.settings.answer.evidence_k = self.config.evidence_k
        self.settings.answer.max_concurrent_requests = (
            self.config.max_concurrent_requests
        )

        load_started = time.perf_counter()
        self.embedding_model = stage("embedding_model_load", self.settings.get_embedding_model)
        self.embedding_model_load_ms = round(
            (time.perf_counter() - load_started) * 1000, 3
        )
        self.embedding_model_load_count = 1

    async def analyze(self, paper: PaperRef) -> ProviderAnalysis:
        if paper.pdfUrl is None:
            raise FullTextUnavailableError("PaperRef.pdfUrl is required for full-text analysis")

        total_started = time.perf_counter()
        download_started = time.perf_counter()
        pdf_bytes = await self._fetch_pdf(paper.pdfUrl)
        download_finished = time.perf_counter()

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(pdf_bytes)
                temp_path = Path(handle.name)

            docs = self._docs_type()
            citation = paper.title or paper.doi or paper.url or paper.pdfUrl
            add_started = time.perf_counter()
            try:
                docname = await docs.aadd(
                    temp_path,
                    citation=citation,
                    settings=self.settings,
                    embedding_model=self.embedding_model,
                )
            except (OSError, ValueError) as exc:
                raise FullTextUnavailableError(
                    "PaperQA could not parse the downloaded PDF"
                ) from exc
            add_finished = time.perf_counter()
            if docname is None:
                raise FullTextUnavailableError("PaperQA did not add the downloaded PDF")

            query_started = time.perf_counter()
            session = await docs.aquery(
                ANALYSIS_QUESTION,
                settings=self.settings,
                embedding_model=self.embedding_model,
            )
            query_finished = time.perf_counter()

            contexts = [
                ProviderContext(
                    context_id=context.id,
                    locator=context.text.name,
                    raw_text=context.text.text,
                    summary=context.context,
                    score=context.score,
                    source=context.text.doc.formatted_citation,
                )
                for context in session.contexts
            ]
            if not contexts:
                raise StructuredAnalysisError("PaperQA returned no evidence contexts")

            raw_answer = session.raw_answer or session.answer
            display_answer = session.answer or session.formatted_answer or raw_answer
            citation_incomplete = bool(PQAC_PATTERN.search(display_answer))
            clean_answer = self.sanitize_answer(display_answer)
            parser = self.settings.parsing.parse_pdf
            parser_name = (
                f"{getattr(parser, '__module__', type(parser).__module__)}."
                f"{getattr(parser, '__qualname__', type(parser).__qualname__)}"
            )
            return ProviderAnalysis(
                answer=clean_answer,
                contexts=contexts,
                references=session.references,
                timings={
                    "downloadMs": round(
                        (download_finished - download_started) * 1000, 3
                    ),
                    "parseChunkEmbedMs": round(
                        (add_finished - add_started) * 1000, 3
                    ),
                    "retrieveSummarizeAnswerMs": round(
                        (query_finished - query_started) * 1000, 3
                    ),
                    "totalMs": round((query_finished - total_started) * 1000, 3),
                },
                diagnostics={
                    "rawAnswer": raw_answer,
                    "formattedAnswer": session.formatted_answer,
                    "citationFormattingIncomplete": citation_incomplete,
                    "referencesEmpty": not bool(session.references.strip()),
                    "evidenceCount": len(contexts),
                    "parser": parser_name,
                    "embedding": self.config.embedding,
                    "generationModel": self.config.model,
                    "summaryModel": self.config.model,
                    "evidenceK": self.settings.answer.evidence_k,
                    "summaryConcurrency": self.settings.answer.max_concurrent_requests,
                    "automaticRetries": 0,
                },
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    async def _fetch_pdf(self, url: str) -> bytes:
        if self._pdf_fetcher is not None:
            try:
                content = await self._pdf_fetcher(url)
            except httpx.HTTPError as exc:
                raise FullTextUnavailableError("pdfUrl could not be downloaded") from exc
        else:
            try:
                async with httpx.AsyncClient(
                    timeout=self.config.download_timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    content = response.content
            except httpx.HTTPError as exc:
                raise FullTextUnavailableError("pdfUrl could not be downloaded") from exc
        if len(content) > self.config.max_pdf_bytes:
            raise FullTextUnavailableError("PDF exceeds the configured size limit")
        if not content.lstrip().startswith(b"%PDF-"):
            raise FullTextUnavailableError("pdfUrl did not return a PDF document")
        return content

    @staticmethod
    def sanitize_answer(answer: str) -> str:
        cleaned = re.sub(
            r"\(?\s*pqac-[0-9a-f]{8}(?:\s*,\s*pqac-[0-9a-f]{8})*\s*\)?",
            "",
            answer,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r" {2,}", " ", cleaned)
        return cleaned.strip()
