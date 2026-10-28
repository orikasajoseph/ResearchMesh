"""Verify existing S1 and separate S2 evidence and derive a contextual S3 task."""
from pathlib import Path
from group_literature_leader import validate_repository_chain_task
from platform_chain_resources import validate_preparation
from platform_three_agent_orchestrator import (
    bind_business_task, digest, read_json, validate_chain, validate_completed_record,
    verify_saved_stage,
)


def build_contextual_handoff(chain, reader, proof):
    chain, reader = Path(chain), Path(reader)
    state, plan = read_json(chain / 'state.json'), read_json(chain / 'plan.json')
    tasks = validate_chain(plan)
    if digest(plan) != state.get('planSha256'):
        raise ValueError('FORMAL_CHAIN_PLAN_CHANGED')
    rows = [x for x in state.get('stages', []) if x.get('stage') == 'literature_search']
    if len(rows) != 1 or rows[0].get('state') != 'completed':
        raise ValueError('FORMAL_S1_NOT_COMPLETED')
    s1_task = bind_business_task(tasks[0], {})
    s1_record, s1_result = verify_saved_stage(chain, rows[0], s1_task)
    if s1_record.get('scope') != 'PLATFORM_LITERATURE_SINGLE_TASK':
        raise ValueError('FORMAL_S1_REQUIRED')
    s2_bound = bind_business_task(tasks[1], {tasks[0]['subtaskId']: s1_result})
    s2_task = validate_preparation(read_json(proof), s2_bound, s1_result)
    s2_record = read_json(reader / 'record.json')
    if s2_record.get('scope') != 'PLATFORM_READER_SINGLE_TASK':
        raise ValueError('FORMAL_S2_REQUIRED')
    s2_result = validate_completed_record(s2_record, s2_task)
    if read_json(reader / 'result.json') != s2_result:
        raise ValueError('FORMAL_S2_SAVED_RESULT_MISMATCH')
    s3_task = validate_repository_chain_task(bind_business_task(
        tasks[2], {tasks[1]['subtaskId']: s2_result}))
    return s3_task, {
        'scope': 'MANUAL_RECOVERY_HANDOFF_NOT_UNINTERRUPTED_CHAIN',
        's1GroupId': s1_record['groupId'], 's1TaskId': s1_record['taskId'],
        's1ResultSha256': digest(s1_result), 's2GroupId': s2_record['groupId'],
        's2SessionId': s2_record['sessionId'], 's2TaskId': s2_record['taskId'],
        's2RecordSha256': digest(s2_record), 's2ResultSha256': digest(s2_result),
        'resourceProofSha256': digest(read_json(proof)), 's3TaskSha256': digest(s3_task),
        'readerModelRequestCountVerified': False,
    }
