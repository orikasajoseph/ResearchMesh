"""Explicit composite adapter: consume paper notes without changing Zhang's core.

Only deterministic lexical/identifier comparison, NOT semantic reproduction analysis.
The native CodeWiki request stays strict; no note is silently dropped.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

from common.models import BusinessTask, PlannerTask
from input_binding import validate_task_input, paper_ref
from repository_adapter import to_repository_request, RepositoryAdapterError
from result_contract import validate_result

MODE = 'DETERMINISTIC_LEXICAL_PAPER_REPOSITORY_COMPARISON_NO_LLM'


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def prepare_repository_task(payload):
    """Validate original context first, then split into native static task + consumed notes."""
    model = PlannerTask if {'requiredSkill', 'dependsOn'} & payload.keys() else BusinessTask
    task = model.model_validate(payload).model_dump()
    validate_task_input(task, dispatch=True)
    data = task['input']
    has_context = bool(data.get('papers') or data.get('paperNotes') or task['constraints'].get('requirePaperNotes'))
    if not has_context:
        return task, to_repository_request(task), []
    notes, refs = data.get('paperNotes', []), data.get('papers', [])
    if len(notes) != 1 or len(refs) != 1:
        raise RepositoryAdapterError('SINGLE_FULLTEXT_PAPER_CONTEXT_REQUIRED')
    note = notes[0]
    read_task = {'schemaVersion': '0.2', 'subtaskId': 'context-validation', 'taskType': 'paper_read'}
    validate_result({'schemaVersion': '0.2', 'subtaskId': 'context-validation', 'taskType': 'paper_read',
                     'summary': 'Validate supplied paper context', 'items': [{'kind': 'paper_note', 'data': note}], 'warnings': []}, read_task)
    if note.get('isMock') is True or note.get('analysisLevel') != 'fulltext':
        raise RepositoryAdapterError('REAL_FULLTEXT_PAPER_CONTEXT_REQUIRED')
    if paper_ref(note['source']) != paper_ref(refs[0]):
        raise RepositoryAdapterError('PAPER_CONTEXT_SOURCE_MISMATCH')
    if len(json.dumps(notes, ensure_ascii=False).encode('utf-8')) > 160_000:
        raise RepositoryAdapterError('PAPER_CONTEXT_TOO_LARGE')
    # Only this composite adapter may strip context: attach_comparison below consumes it.
    static = deepcopy(task)
    static['input'] = {'repositoryUrls': list(data.get('repositoryUrls', []))}
    static['constraints']['requirePaperNotes'] = False
    native = to_repository_request(static)
    return static, native, deepcopy(notes)


_STOP = set('the and for with from this that which where into while only paper method findings limitations results model models example examples using used uses their these those shows show based different methods training learning language parameters weight weights'.split())


def _words(text):
    return re.findall(r'[A-Za-z][A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}', text)


def _terms(text):
    words = _words(text)
    pairs = [a.lower() + ' ' + b.lower() for a, b in zip(words, words[1:])
             if a.lower() not in _STOP and b.lower() not in _STOP]
    singles = [w.lower() for w in words if w.lower() not in _STOP and
               (len(w) >= 8 or (len(w) >= 3 and any(c.isupper() for c in w[1:])))]
    return list(dict.fromkeys(pairs + singles))[:80]


def _identifiers(source):
    values = []
    if source.get('doi'):
        values.append(source['doi'].lower())
    for key in ('url', 'pdfUrl'):
        for match in re.finditer(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?', source.get(key) or '', re.I):
            values.append(match.group(1))
    return list(dict.fromkeys(values))


def compare_notes(notes, evidence):
    normalized = [(e, ' ' + ' '.join(w.lower() for w in _words(e.get('text') or '')) + ' ') for e in evidence]
    comparisons = []
    for note in notes:
        identifiers = _identifiers(note['source'])
        links = [{'identifier': identity, 'repositoryEvidenceId': e['sourceId']}
                 for identity in identifiers for e in evidence
                 if re.search(r'(?<![\w.])' + re.escape(identity) + r'(?!\w|\.\d)', e.get('text') or '', re.I)]
        rows = []
        for field in ('method', 'findings', 'limitations'):
            candidates = _terms(note[field]); matches = []
            for e, text in normalized:
                found = [term for term in candidates if ' ' + term + ' ' in text]
                if found:
                    matches.append({'repositoryEvidenceId': e['sourceId'], 'terms': found[:8]})
                if len(matches) >= 8:
                    break
            rows.append({'field': field, 'paperText': note[field], 'matches': matches,
                         'observation': 'lexical_matches_only' if matches else 'no_lexical_match_in_extracted_evidence',
                         'semanticClaimVerified': False})
        comparisons.append({'source': deepcopy(note['source']), 'noteSha256': digest(note),
            'paperEvidence': [{key: e[key] for key in ('sourceId', 'url', 'locator', 'supports')} for e in note['evidence']],
            'repositoryIdentifierMentions': links, 'rows': rows})
    return {'mode': MODE, 'inputNotesSha256': digest(notes), 'paperCount': len(notes), 'papers': comparisons,
            'semanticReproductionAnalysis': False, 'reproductionVerified': False,
            'limitations': ['Lexical overlap does not verify implementation, equivalence, experiments or performance.',
                           'No match does not prove absence: only extracted static evidence was searched.',
                           'An identifier mention does not prove this is the authors official repository.']}


def attach_comparison(result, original_payload, notes):
    if not notes:
        return result
    output = deepcopy(result)
    data = output['items'][0]['data']
    data['paperComparison'] = compare_notes(notes, data['evidence'])
    count = sum(len(row['matches']) for p in data['paperComparison']['papers'] for row in p['rows'])
    output['summary'] += f' Paper context consumed: {len(notes)} fulltext note; {count} lexical evidence matches (not semantic verification).'
    output['warnings'].append('PAPER_CONTEXT_LEXICAL_ONLY_NOT_REPRODUCTION_VERIFICATION')
    validate_result(output, original_payload)
    return output


def comparison_valid(result, payload):
    try:
        _, _, notes = prepare_repository_task(payload)
        data = result['items'][0]['data']
        return bool(notes and data.get('paperComparison') == compare_notes(notes, data['evidence']))
    except (ValueError, KeyError, TypeError):
        return False
