"""Explicit optional enhancement over one static result, using Zhang's Enricher.

No provider is constructed here until the worker explicitly supplies a factory.
Static analysis is not rerun. A failed model cannot discard the static result.
"""
import time

from repository_adapter import normalize_repository_result
from repository_enhancement_contract import project_paper_notes, validate_enhancement_evidence
from repository_paper_context import prepare_repository_task


def prepare_enhanced_request(payload, wrapper):
    static_payload, request, notes = prepare_repository_task(payload)
    request['input']['paperNotes'] = project_paper_notes(notes)
    return static_payload, wrapper.RepositoryAnalyzeRequest.model_validate(request)


def enrich_static_result(payload, raw, wrapper, provider_factory, observations):
    static_payload, request = prepare_enhanced_request(payload, wrapper)
    result = raw.model_copy(deep=True)
    record = result.items[0].data
    record.paperEvidence = [e for note in request.input.paperNotes for e in note.evidence]
    started = time.perf_counter()
    warnings = []
    try:
        provider = provider_factory(observations)
        enriched = wrapper.RepositoryEnricher(provider).enrich(
            record.evidence, entrypoints=[e.model_dump() for e in record.entrypoints],
            run_commands=[c.model_dump() for c in record.runCommands],
            dependency_summary={'edgeCount': len(record.dependencies)},
            paper_notes=request.input.paperNotes)
        record.overview = enriched.overview
        record.reproductionBarriers = enriched.reproduction_barriers
        record.paperCodeRelations = enriched.paper_code_relations
        warnings = enriched.warnings
        status = 'unavailable' if 'LLM_ENRICHMENT_UNAVAILABLE' in warnings else 'completed'
    except Exception:
        # No exception message, prompt, key, or provider response reaches the AIP peer.
        record.overview = None
        record.reproductionBarriers = []
        record.paperCodeRelations = []
        warnings = ['LLM_ENRICHMENT_UNAVAILABLE']
        status = 'unavailable'
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    record.diagnostics.metrics.llmEnrichmentMs = elapsed
    record.diagnostics.metrics.totalMs += elapsed
    result.warnings = list(dict.fromkeys([*result.warnings, *warnings]))
    normalized = normalize_repository_result(static_payload, result, wrapper, context_payload=payload)
    validate_enhancement_evidence(normalized['items'][0]['data'], payload)
    relation_count = len(record.paperCodeRelations)
    has_claims = bool(record.overview or record.reproductionBarriers or relation_count)
    if status == 'completed' and (not has_claims or (request.input.paperNotes and not relation_count)):
        status = 'no_supported_claims'
    observations.update(enrichmentStatus=status, paperCodeRelationCount=relation_count,
                        paperContextAnalyzed=bool(request.input.paperNotes and relation_count),
                        reproductionVerified=False)
    normalized['items'][0]['data']['enrichment'] = {
        'requested': True, 'status': status,
        'paperContextSupplied': bool(request.input.paperNotes),
        'paperCodeRelationCount': relation_count,
        'reproductionVerified': False,
    }
    return normalized
