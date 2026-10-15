"""Revalidate previous real AIP Reader; bind completed S2 result to a new S3.

Never replay Planner, search, PDF download or paid Reader; retain original IDs.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from assemble_aip_evidence import assemble
from input_binding import bind_task_input, github_repository_url
from planner_contract import validate_plan
from repository_adapter import RepositoryAdapterError
from repository_paper_context import prepare_repository_task, digest


def load_reader_source(source, *, cache_dir):
    source = Path(source).resolve()
    if not (source / 'reader_input.json').is_file() and (source / 'reader/reader_input.json').is_file():
        source = source / 'reader'
    saved = json.loads((source / 'reader_input.json').read_text(encoding='utf-8'))
    combined = assemble(saved['searchSource'], source, cache_dir=cache_dir)
    row = next(r for r in combined['executionRecords'] if r['result']['taskType'] == 'paper_read')
    outcome = json.loads((source / 'outcome.json').read_text(encoding='utf-8'))
    check_reader_products(outcome, row['result'])
    if len(row['result']['items']) != 1:
        raise RepositoryAdapterError('SINGLE_FULLTEXT_PAPER_CONTEXT_REQUIRED')
    # Freeze all source records plus actual PDF/proof; never copy/modify originals.
    manifest = deepcopy(combined['sourceManifest'])
    for value in (source / 'reader_input.json', Path(saved['pdf']['path']), Path(saved['pdfProofSource'])):
        manifest.append({'path': str(value.resolve()), 'sha256': sha256(value.read_bytes()).hexdigest()})
    verify_manifest(manifest)
    return {'scope': 'VERIFIED_PREVIOUS_REAL_AIP_READER_NOT_REPLAYED', 'readerSource': str(source),
            'readerTask': deepcopy(saved['payload']), 'readerRecord': row, 'sourceManifest': manifest,
            'readerResultSha256': digest(row['result'])}


def check_reader_products(outcome, expected):
    observed = False
    for row in outcome['aipMessages']:
        products = row['response']['result'].get('products') or []
        if products:
            data = [item['data'] for product in products for item in product.get('dataItems', []) if item.get('type') == 'data']
            if data != [expected]:
                raise RepositoryAdapterError('READER_AIP_PRODUCT_MISMATCH')
            observed = True
    if not observed:
        raise RepositoryAdapterError('READER_AIP_PRODUCT_REQUIRED')


def verify_manifest(manifest):
    if not manifest:
        raise RepositoryAdapterError('READER_SOURCE_MANIFEST_REQUIRED')
    for item in manifest:
        if sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise RepositoryAdapterError('READER_SOURCE_CHANGED')


def build_handoff(checkpoint, repository_url):
    if not github_repository_url(repository_url):
        raise RepositoryAdapterError('INVALID_REPOSITORY_URL')
    previous = checkpoint['readerRecord']['result']
    if digest(previous) != checkpoint['readerResultSha256']:
        raise RepositoryAdapterError('READER_SOURCE_CHANGED')
    task_id = 'S3' if previous['subtaskId'] != 'S3' else 'S3-repository'
    dependent = {'schemaVersion': '0.2', 'subtaskId': task_id, 'taskType': 'repository_analyze',
        'goal': '将已完成全文笔记与指定仓库静态证据对照，列出待人工核验项；不推断复现成功。',
        'input': {'repositoryUrls': [repository_url]},
        'constraints': {'maxRepositories': 1, 'executeCode': False, 'allowRepositorySearch': False, 'requirePaperNotes': True},
        'outputSchema': 'code_records.v1', 'requiredSkill': 'repository.analyze', 'dependsOn': [previous['subtaskId']]}
    bound = bind_task_input(dependent, {previous['subtaskId']: deepcopy(previous)})
    prepare_repository_task(bound)
    # Parent dependency was verified complete. Standalone execution must not replay S2.
    submitted = deepcopy(bound); submitted['dependsOn'] = []
    plan = {'schemaVersion': '0.2', 'userQuery': '复用已完成论文阅读，对照指定仓库 ' + repository_url + '；不调用模型。',
            'needsClarification': False, 'clarificationQuestion': None, 'tasks': [submitted]}
    validate_plan(plan)
    return {'plan': plan, 'dependentTaskBeforeBinding': dependent, 'boundTaskBeforeDispatch': bound,
            'dependencyResolvedFromPreviousCompletedTask': True, 'previousReaderAipTaskId': checkpoint['readerRecord']['aipTaskId']}
