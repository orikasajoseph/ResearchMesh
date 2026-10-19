"""Bounded paper projection and evidence checks; no Partner/provider imports.

Projection matches Zhang's 22863c0 IDs. Task v0.2 and the seven-field envelope
are unchanged. This release retains Zhao's one-fulltext-paper input boundary.
"""
from hashlib import sha256
import json
import re

from input_binding import paper_ref, web_url


def project_paper_notes(notes):
    if not isinstance(notes, list) or len(notes) > 1:
        raise ValueError('SINGLE_FULLTEXT_PAPER_CONTEXT_REQUIRED')
    projected = []
    for raw in notes:
        if not isinstance(raw, dict) or raw.get('isMock') is True or raw.get('analysisLevel') != 'fulltext':
            raise ValueError('REAL_FULLTEXT_PAPER_CONTEXT_REQUIRED')
        source = paper_ref(raw.get('source'))
        if any(not isinstance(raw.get(k), str) or not raw[k].strip()
               for k in ('method', 'findings', 'limitations')):
            raise ValueError('INVALID_PAPER_NOTES')
        evidence = raw.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('PAPER_EVIDENCE_REQUIRED')
        selected = []
        # Validate ALL supplied evidence, even though only the first six are sent.
        for index, item in enumerate(evidence):
            if (not isinstance(item, dict) or item.get('sourceId') != source['paperId']
                    or item.get('url') != (source['pdfUrl'] or source['url'])
                    or not web_url(item.get('url'))
                    or any(not isinstance(item.get(k), str) or not item[k].strip()
                           for k in ('locator', 'supports'))
                    or (item.get('text') is not None and not isinstance(item['text'], str))):
                raise ValueError('PAPER_EVIDENCE_SOURCE_MISMATCH')
            if index >= 6:
                continue
            identity = json.dumps([source['paperId'], index, item['url'], item['locator'],
                                   item['supports']], ensure_ascii=False)
            selected.append({'evidenceId': 'P-' + sha256(identity.encode('utf-8')).hexdigest()[:16],
                             'sourceId': item['sourceId'], 'url': item['url'],
                             'locator': item['locator'][:300], 'supports': item['supports'][:1200],
                             'text': item['text'][:1200] if item.get('text') else None})
        projected.append({'source': source, 'method': raw['method'][:2000],
                          'findings': raw['findings'][:2000], 'limitations': raw['limitations'][:2000],
                          'analysisLevel': 'fulltext', 'evidence': selected})
    return projected


def validate_enhancement_evidence(data, task):
    """Check normalized repository data, including both sides of each relation.

No LLM/CodeWiki/PaperQA is imported by Leader validation. Evidence-ID validity
does not establish scientific correctness or successful code reproduction.
"""
    expected = {e['evidenceId']: e for note in project_paper_notes(
        task.get('input', {}).get('paperNotes', [])) for e in note['evidence']}
    paper_evidence, relations = data.get('paperEvidence', []), data.get('paperCodeRelations', [])
    if not isinstance(paper_evidence, list) or not isinstance(relations, list):
        raise ValueError('INVALID_PAPER_CODE_RELATION')
    actual = {}
    for item in paper_evidence:
        if not isinstance(item, dict) or not isinstance(item.get('evidenceId'), str):
            raise ValueError('UNSUPPORTED_PAPER_EVIDENCE')
        eid = item['evidenceId']
        if eid in actual or item != expected.get(eid):
            raise ValueError('UNSUPPORTED_PAPER_EVIDENCE')
        actual[eid] = item
    if actual != expected:
        raise ValueError('PAPER_EVIDENCE_INCOMPLETE')
    code_ids = {e.get('sourceId') for e in data.get('evidence', []) if isinstance(e, dict)}
    def valid_ids(value, known):
        return (isinstance(value, list) and bool(value)
                and all(isinstance(eid, str) and eid in known for eid in value))
    for item in relations:
        if (not isinstance(item, dict) or not isinstance(item.get('claim'), str)
                or not item['claim'].strip()
                or re.search(r'\b(?:executed|ran|reproduced|replicated)\b|已(?:执行|运行|复现)|成功复现',
                             item['claim'], re.IGNORECASE)
                or not valid_ids(item.get('paperEvidenceIds'), actual)
                or not valid_ids(item.get('codeEvidenceIds'), code_ids)):
            raise ValueError('UNSUPPORTED_PAPER_CODE_RELATION')
