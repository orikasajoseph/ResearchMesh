"""Offline checks for the formal contextual Repository handoff."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import platform_contextual_handoff as handoff
import run_platform_repository as cli
from tests.test_group_invitation import LEADER, PARTNER
from tests.test_platform_repository import contextual_task
from tests.test_platform_three_agent_orchestrator import plan


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan()
        self.state = {'planSha256': handoff.digest(self.plan), 'stages': [
            {'stage': 'literature_search', 'state': 'completed'}]}
        self.s1 = {'scope': 'PLATFORM_LITERATURE_SINGLE_TASK', 'groupId': 'g1', 'taskId': 't1'}
        self.s2 = {'scope': 'PLATFORM_READER_SINGLE_TASK', 'groupId': 'g2',
                   'sessionId': 's2', 'taskId': 't2'}
        self.result = {'items': [{'kind': 'paper_note'}]}

    def run_handoff(self, *, saved_result=None):
        mapping = {'state.json': self.state, 'plan.json': self.plan, 'proof.json': {'proof': True},
                   'record.json': self.s2, 'result.json': self.result if saved_result is None else saved_result}
        with patch.object(handoff, 'read_json', side_effect=lambda path: deepcopy(mapping[Path(path).name])), \
             patch.object(handoff, 'verify_saved_stage', return_value=(self.s1, {'items': []})), \
             patch.object(handoff, 'bind_business_task', side_effect=[{'s1': 1}, {'s2': 1}, contextual_task()]), \
             patch.object(handoff, 'validate_preparation', return_value={'s2': 1}), \
             patch.object(handoff, 'validate_completed_record', return_value=self.result):
            return handoff.build_contextual_handoff('chain', 'reader', 'proof.json')

    def test_accepts_verified_formal_sources_and_labels_recovery(self):
        task, evidence = self.run_handoff()
        self.assertEqual(task, contextual_task())
        self.assertEqual(evidence['scope'], 'MANUAL_RECOVERY_HANDOFF_NOT_UNINTERRUPTED_CHAIN')
        self.assertFalse(evidence['readerModelRequestCountVerified'])

    def test_mismatched_saved_result_and_changed_plan_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'FORMAL_S2_SAVED_RESULT_MISMATCH'):
            self.run_handoff(saved_result={'items': []})
        self.state['planSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'FORMAL_CHAIN_PLAN_CHANGED'):
            self.run_handoff()

    def test_nonformal_s1_or_s2_is_rejected(self):
        self.s1['scope'] = 'OFFLINE'
        with self.assertRaisesRegex(ValueError, 'FORMAL_S1_REQUIRED'):
            self.run_handoff()
        self.s1['scope'] = 'PLATFORM_LITERATURE_SINGLE_TASK'
        self.s2['scope'] = 'OFFLINE'
        with self.assertRaisesRegex(ValueError, 'FORMAL_S2_REQUIRED'):
            self.run_handoff()


class ContextualCliTests(unittest.TestCase):
    def args(self, directory):
        return ['--leader-aic', LEADER, '--partner-aic', PARTNER, '--credentials-dir', directory,
                '--context-chain', 'chain', '--context-reader', 'reader', '--context-proof', 'proof']

    def test_live_requires_manual_release_before_any_group(self):
        with TemporaryDirectory() as directory, patch.object(cli, 'run_group_repository_chain') as runner, \
                patch.object(cli, 'build_contextual_handoff') as build, patch('builtins.print'):
            self.assertEqual(cli.main(self.args(directory) + ['--execute', '--partner-ready']), 2)
            build.assert_not_called()
            runner.assert_not_called()

    def test_offline_uses_contextual_runner_and_writes_evidence(self):
        async def offline(config, task, **kwargs):
            self.assertFalse(kwargs['execute'])
            self.assertEqual(task, contextual_task())
            record = {'scope': 'OFFLINE_PLATFORM_REPOSITORY_CHAIN_PREFLIGHT', 'groupId': 'g',
                      'taskId': 't', 'sessionId': 's', 'events': [], 'result': None,
                      'passed': True, 'protocolCompleted': False, 'businessReady': False,
                      'cleanupSucceeded': None, 'error': None, 'remoteStateUnknown': False}
            kwargs['checkpoint'](record)
            return record
        with TemporaryDirectory() as directory, patch.object(cli, 'ROOT', Path(directory)), \
                patch.object(cli, 'build_contextual_handoff', return_value=(contextual_task(), {'scope': 'test'})), \
                patch.object(cli, 'run_group_repository_chain', side_effect=offline), patch('builtins.print'):
            self.assertEqual(cli.main(self.args(directory)), 0)
            self.assertEqual(len(list(Path(directory).glob('outputs/platform-repository/*/handoff.json'))), 1)


if __name__ == '__main__':
    unittest.main()
