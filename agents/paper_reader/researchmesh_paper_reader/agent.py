from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .provider import (
    FullTextUnavailableError,
    PaperAnalysisProvider,
    PaperQAProvider,
    ProviderAnalysis,
    ProviderContext,
    StructuredAnalysisError,
)
from .schemas import (
    PaperEvidence,
    PaperNoteData,
    PaperNoteItem,
    PaperReadResult,
    PaperReadTask,
    PaperRef,
)


FULL_TEXT_UNAVAILABLE = "FULL_TEXT_UNAVAILABLE"
PAPERQA_CITATION_FORMATTING_INCOMPLETE = (
    "PAPERQA_CITATION_FORMATTING_INCOMPLETE"
)
PAPERQA_STRUCTURED_OUTPUT_INCOMPLETE = "PAPERQA_STRUCTURED_OUTPUT_INCOMPLETE"

SECTION_PATTERN = re.compile(
    r"(?im)^\s*(RESEARCH_PROBLEM|METHOD|FINDINGS|LIMITATIONS)\s*:\s*"
)
PQAC_CITATION_PATTERN = re.compile(
    r"\(?\s*pqac-[0-9a-f]{8}(?:\s*,\s*pqac-[0-9a-f]{8})*\s*\)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PaperSections:
    problem: str
    method: str
    findings: str
    limitations: str


class PaperReaderAgent:
    skill = "paper.analyze"

    def __init__(self, provider: PaperAnalysisProvider | None = None) -> None:
        self.provider = provider or PaperQAProvider()
        self.last_provider_analysis: ProviderAnalysis | None = None

    async def run(
        self, task_payload: Mapping[str, Any] | PaperReadTask
    ) -> PaperReadResult:
        task = (
            task_payload
            if isinstance(task_payload, PaperReadTask)
            else PaperReadTask.model_validate(task_payload)
        )
        paper = task.input.papers[0]
        if paper.pdfUrl is None:
            return PaperReadResult(
                subtaskId=task.subtaskId,
                summary="Full text is unavailable; no paper analysis was generated.",
                warnings=[FULL_TEXT_UNAVAILABLE],
            )

        try:
            analysis = await self.provider.analyze(paper)
            self.last_provider_analysis = analysis
        except FullTextUnavailableError:
            return PaperReadResult(
                subtaskId=task.subtaskId,
                summary="Full text is unavailable; no paper analysis was generated.",
                warnings=[FULL_TEXT_UNAVAILABLE],
            )

        warnings: list[str] = []
        if analysis.diagnostics.get("citationFormattingIncomplete"):
            warnings.append(PAPERQA_CITATION_FORMATTING_INCOMPLETE)

        try:
            sections = self._extract_sections(self._sanitize_business_answer(analysis.answer))
        except StructuredAnalysisError:
            return PaperReadResult(
                subtaskId=task.subtaskId,
                summary="PaperQA returned evidence but not a complete structured paper note.",
                warnings=warnings + [PAPERQA_STRUCTURED_OUTPUT_INCOMPLETE],
            )

        evidence = [
            self._map_evidence(paper, context, sections, analysis)
            for context in analysis.contexts
        ]
        item = PaperNoteItem(
            data=PaperNoteData(
                source=paper,
                method=sections.method,
                findings=sections.findings,
                limitations=sections.limitations,
                analysisLevel="fulltext",
                evidence=evidence,
            )
        )
        return PaperReadResult(
            subtaskId=task.subtaskId,
            summary=sections.problem,
            items=[item],
            warnings=warnings,
        )

    @staticmethod
    def _sanitize_business_answer(answer: str) -> str:
        cleaned = PQAC_CITATION_PATTERN.sub("", answer)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r" {2,}", " ", cleaned)
        return cleaned.strip()

    @staticmethod
    def _extract_sections(answer: str) -> PaperSections:
        matches = list(SECTION_PATTERN.finditer(answer))
        values: dict[str, str] = {}
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
            value = answer[start:end].strip()
            if match.group(1) == "LIMITATIONS":
                value = re.split(r"(?im)^\s*References\s*$", value, maxsplit=1)[0].strip()
            values[match.group(1)] = value

        required = {"RESEARCH_PROBLEM", "METHOD", "FINDINGS", "LIMITATIONS"}
        if set(values) != required or any(not values[key] for key in required):
            raise StructuredAnalysisError("PaperQA answer is missing structured sections")
        return PaperSections(
            problem=values["RESEARCH_PROBLEM"],
            method=values["METHOD"],
            findings=values["FINDINGS"],
            limitations=values["LIMITATIONS"],
        )

    @classmethod
    def _map_evidence(
        cls,
        paper: PaperRef,
        context: ProviderContext,
        sections: PaperSections,
        analysis: ProviderAnalysis,
    ) -> PaperEvidence:
        raw_answer = str(analysis.diagnostics.get("rawAnswer") or analysis.answer)
        section_values = {
            "method": sections.method,
            "findings": sections.findings,
            "limitations": sections.limitations,
        }
        supports = [
            name
            for name, value in section_values.items()
            if context.context_id in value
            or context.locator in value
            or (
                context.context_id in raw_answer
                and cls._section_mentions_context(raw_answer, name, context.context_id)
            )
        ]
        if not supports:
            supports = cls._infer_supports(context)
        return PaperEvidence(
            sourceId=paper.paperId,
            url=paper.pdfUrl,
            locator=context.locator,
            supports=supports,
            text=context.raw_text,
            summary=context.summary,
            score=context.score,
        )

    @staticmethod
    def _section_mentions_context(answer: str, section: str, context_id: str) -> bool:
        label = {
            "method": "METHOD",
            "findings": "FINDINGS",
            "limitations": "LIMITATIONS",
        }[section]
        match = re.search(
            rf"(?is){label}\s*:.*?(?=\n\s*(?:RESEARCH_PROBLEM|METHOD|FINDINGS|LIMITATIONS)\s*:|\Z)",
            answer,
        )
        return bool(match and context_id in match.group(0))

    @staticmethod
    def _infer_supports(context: ProviderContext) -> list[str]:
        text = f"{context.summary}\n{context.raw_text}".lower()
        supports: list[str] = []
        if any(
            term in text
            for term in ("method", "approach", "model", "framework", "方法", "模型")
        ):
            supports.append("method")
        if any(
            term in text
            for term in ("result", "finding", "show", "conclusion", "发现", "结果", "结论")
        ):
            supports.append("findings")
        if any(
            term in text
            for term in (
                "limitation",
                "challenge",
                "open question",
                "lack of",
                "trade-off",
                "局限",
                "挑战",
                "缺乏",
            )
        ):
            supports.append("limitations")
        return supports or ["findings"]
