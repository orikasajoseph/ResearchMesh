from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .schemas import (Evidence, Overview, PaperCodeRelation, RepositoryPaperNote,
                      ReproductionBarrier)


class StructuredLLM(Protocol):
    def generate_json(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class EnrichmentResult:
    overview: Overview | None
    reproduction_barriers: list[ReproductionBarrier]
    warnings: list[str]
    latency_ms: float
    paper_code_relations: list[PaperCodeRelation] = field(default_factory=list)


class RepositoryEnricher:
    def __init__(self, provider: StructuredLLM, max_evidence_chars: int = 32_000) -> None:
        self.provider = provider
        self.max_evidence_chars = max_evidence_chars

    def enrich(
        self,
        evidence: list[Evidence],
        *,
        entrypoints: list[dict[str, Any]],
        run_commands: list[dict[str, Any]],
        dependency_summary: dict[str, int],
        paper_notes: list[RepositoryPaperNote] | None = None,
    ) -> EnrichmentResult:
        started = time.perf_counter()
        selected = self._select_evidence(evidence)
        known_ids = {item.evidenceId for item in selected}
        notes = paper_notes or []
        paper_ids = {item.evidenceId for note in notes for item in note.evidence}
        bundle = {
            "evidence": [item.model_dump() for item in selected],
            "entrypoints": entrypoints,
            "runCommands": run_commands,
            "dependencySummary": dependency_summary,
        }
        if notes:
            bundle["paperNotes"] = [note.model_dump(mode="json") for note in notes]
        schema = {
            "type": "object",
            "properties": {
                "overview": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "evidenceIds": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["text", "evidenceIds"],
                        },
                    ]
                },
                "reproductionBarriers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue": {"type": "string"},
                            "evidenceIds": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["issue", "evidenceIds"],
                    },
                },
            },
            "required": ["overview", "reproductionBarriers"],
        }
        if notes:
            schema["properties"]["paperCodeRelations"] = {
                "type": "array", "items": {"type": "object", "properties": {
                    "claim": {"type": "string"},
                    "paperEvidenceIds": {"type": "array", "items": {"type": "string"}},
                    "codeEvidenceIds": {"type": "array", "items": {"type": "string"}},
                }, "required": ["claim", "paperEvidenceIds", "codeEvidenceIds"]}}
            schema["required"].append("paperCodeRelations")
        prompt = (
            "Summarize only facts supported by the supplied evidence. Produce an overview and "
            "concrete reproduction barriers. Every claim must cite one or more supplied evidenceIds. "
            "Do not invent IDs, commands, requirements, or barriers. If no barrier is evidenced, "
            "return an empty reproductionBarriers array. "
            + ("For paperCodeRelations, cite both real paperEvidenceIds and codeEvidenceIds; "
               "do not claim code execution or successful reproduction. " if notes else "")
            + "Evidence bundle:\n"
            + json.dumps(bundle, ensure_ascii=False)
        )
        try:
            raw = self.provider.generate_json(
                [{"role": "user", "content": prompt}], schema=schema
            )
        except Exception:  # noqa: BLE001 - enrichment must degrade without losing static facts
            return EnrichmentResult(
                overview=None,
                reproduction_barriers=[],
                warnings=["LLM_ENRICHMENT_UNAVAILABLE"],
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )

        warnings: list[str] = []
        overview = _validated_overview(raw.get("overview"), known_ids, warnings)
        barriers = _validated_barriers(
            raw.get("reproductionBarriers"), known_ids, warnings
        )
        relations = _validated_relations(raw.get("paperCodeRelations"), paper_ids,
                                         known_ids, warnings) if notes else []
        return EnrichmentResult(
            overview=overview,
            reproduction_barriers=barriers,
            warnings=warnings,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            paper_code_relations=relations,
        )

    def _select_evidence(self, evidence: list[Evidence]) -> list[Evidence]:
        accepted = {
            "readme",
            "manifest",
            "dependency_config",
            "environment_example",
            "container_config",
            "ci",
            "artifact",
        }
        selected: list[Evidence] = []
        used = 0
        for item in evidence:
            if item.sourceType not in accepted:
                continue
            remaining = self.max_evidence_chars - used
            if remaining <= 0:
                break
            if len(item.text) <= remaining:
                selected.append(item)
                used += len(item.text)
        return selected


def _valid_ids(value: Any, known_ids: set[str]) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or item not in known_ids for item in value):
        return None
    return list(dict.fromkeys(value))


def _validated_overview(
    value: Any, known_ids: set[str], warnings: list[str]
) -> Overview | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        warnings.append("UNSUPPORTED_LLM_CLAIM")
        return None
    ids = _valid_ids(value.get("evidenceIds"), known_ids)
    if ids is None or not value["text"].strip():
        warnings.append("UNSUPPORTED_LLM_CLAIM")
        return None
    return Overview(text=value["text"].strip(), evidenceIds=ids)


def _validated_barriers(
    value: Any, known_ids: set[str], warnings: list[str]
) -> list[ReproductionBarrier]:
    if not isinstance(value, list):
        if value is not None:
            warnings.append("UNSUPPORTED_LLM_CLAIM")
        return []
    barriers: list[ReproductionBarrier] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("issue"), str):
            warnings.append("UNSUPPORTED_LLM_CLAIM")
            continue
        ids = _valid_ids(item.get("evidenceIds"), known_ids)
        if ids is None or not item["issue"].strip():
            warnings.append("UNSUPPORTED_LLM_CLAIM")
            continue
        barriers.append(
            ReproductionBarrier(issue=item["issue"].strip(), evidenceIds=ids)
        )
    return barriers


def _validated_relations(value: Any, paper_ids: set[str], code_ids: set[str],
                         warnings: list[str]) -> list[PaperCodeRelation]:
    if not isinstance(value, list):
        warnings.append("UNSUPPORTED_PAPER_CODE_CLAIM")
        return []
    relations = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("claim"), str):
            warnings.append("UNSUPPORTED_PAPER_CODE_CLAIM")
            continue
        paper = _valid_ids(item.get("paperEvidenceIds"), paper_ids)
        code = _valid_ids(item.get("codeEvidenceIds"), code_ids)
        claim = item["claim"].strip()
        if (not claim or paper is None or code is None
                or re.search(r"\b(?:executed|ran|reproduced|replicated)\b|已(?:执行|运行|复现)|成功复现",
                             claim, re.IGNORECASE)):
            warnings.append("UNSUPPORTED_PAPER_CODE_CLAIM")
            continue
        relations.append(PaperCodeRelation(claim=claim,
                                           paperEvidenceIds=paper, codeEvidenceIds=code))
    return relations
