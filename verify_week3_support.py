"""Pinned three-party offline regression; never a platform or paid acceptance."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BASELINES = {'zhao': '3ba828e0755f91e789a829c42f7d5df5f4045034',
             'zhang': 'be99b31b3c8411ea88a96d08b0337d6049882046',
             'wu': '58e4e5d884bd4304e7ab3dd8399c1185c7b428eb'}


def suite(name, args, cwd, output):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    completed = subprocess.run([sys.executable, '-X', 'utf8', *args], cwd=cwd, env=env,
                               capture_output=True, text=True, encoding='utf-8', timeout=120)
    log = completed.stdout + completed.stderr
    (output / (name + '.log')).write_text(log, encoding='utf-8')
    match = re.search(r'Ran (\d+) tests', log)
    if match is None:
        match = re.search(r'验收通过：(\d+) 项自动测试', log)
    return {'passed': completed.returncode == 0, 'testsRun': int(match[1]) if match else None,
            'scope': 'OFFLINE_ONLY', 'logFile': name + '.log'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zhang-root', type=Path, required=True, help='完整be99b31副本，不是Wrapper子目录')
    parser.add_argument('--wu-root', type=Path, required=True, help='完整58e4e5d副本')
    args = parser.parse_args()
    output = ROOT / 'outputs' / datetime.now().strftime('week3-support-%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    snapshots = {'zhang': args.zhang_root.resolve(), 'wu': args.wu_root.resolve()}
    # Verify source bytes against fetched Git objects, not merely directory names.
    manifests = {}
    frozen = ['common/models.py', 'common/skills.py', 'input_binding.py', 'result_contract.py',
              'schemas/business_task.schema.json', 'schemas/plan.schema.json', 'schemas/result.schema.json', 'schemas/task.schema.json']
    for path in frozen:
        before = subprocess.check_output(['git', '-C', str(ROOT), 'show', BASELINES['zhao'] + ':' + path])
        if (ROOT / path).read_bytes().replace(b'\r\n', b'\n') != before.replace(b'\r\n', b'\n'):
            raise ValueError('SHARED_CONTRACT_CHANGED_REQUIRES_TEAM_APPROVAL')
    if subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', BASELINES['zhao'], 'HEAD']).returncode:
        raise ValueError('PLANNER_BASELINE_REGRESSED')
    for owner, folder in snapshots.items():
        paths = subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', '-r', '--name-only', BASELINES[owner]]).decode().splitlines()
        selected = [p for p in paths if (p.endswith('.py') and
                    (p.startswith('agents/repository/') if owner == 'zhang' else p.startswith(('aip/', 'tests/test_group_', 'tests/test_three_agent'))))]
        hashes = {}
        for path in selected:
            canonical = subprocess.check_output(['git', '-C', str(ROOT), 'show', BASELINES[owner] + ':' + path])
            actual = (folder / path).read_bytes()
            if actual.replace(b'\r\n', b'\n') != canonical.replace(b'\r\n', b'\n'):
                raise ValueError('UPSTREAM_SNAPSHOT_MISMATCH')
            hashes[path] = hashlib.sha256(canonical).hexdigest()
        manifests[owner] = hashes
    suites = {
        'zhaoFullRegression': suite('zhao-regression', ['verify_project.py'], ROOT, output),
        'zhangPureUnit': suite('zhang-unit', ['-m', 'unittest', 'agents.repository.tests.test_repository_agent.RepositoryAgentUnitTests'], snapshots['zhang'], output),
        'wuGroupAndThreeAgent': suite('wu-offline', ['-m', 'unittest', 'tests.test_group_literature', 'tests.test_three_agent_aip_v02'], snapshots['wu'], output),
        'realWrapperAipSeam': suite('s3-offline', ['verify_repository_seam.py', '--wrapper-root', str(snapshots['zhang'] / 'agents/repository')], ROOT, output),
    }
    record = {'passed': all(s['passed'] for s in suites.values()), 'scope': 'WEEK3_PINNED_SNAPSHOTS_OFFLINE_SUPPORT_NOT_A4_A8',
              'baselines': BASELINES, 'snapshotHashes': manifests, 'suites': suites,
              'frozenContractsUnchanged': True, 'includesPlannerBaseline': True,
              'codewikiRealRegressionRun': False, 'modelCalls': 0, 'platformCalls': 0,
              's3RealAccepted': False, 's2ToS3RealAccepted': False, 'groupMqRealAccepted': False}
    (output / 'acceptance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in record.items() if k != 'snapshotHashes'}, ensure_ascii=False, indent=2))
    print('记录：', output)
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
