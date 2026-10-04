import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest

from compatibility import migrate_plan, MigrationError
from input_binding import bind_task_input, validate_task_input, github_repository_url, InputBindingError
from mock_agents import LiteratureMockAgent, build_mock_agents
from orchestrator import LocalOrchestrator
from planner_contract import validate_plan
from planner import Planner, PlannerError
from resolver import AgentHandle, MockAgentResolver

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/scikit-learn/scikit-learn"


def fixture(legacy=False):
    path = 'legacy_v01_cases.json' if legacy else 'planner_cases.json'
    return json.loads((ROOT/'examples'/path).read_text(encoding='utf-8'))[0]['plan']


class V02Tests(unittest.TestCase):
    def test_planner_cannot_invent_repository_from_research_topic(self):
        plan = fixture()
        plan['tasks'][2]['input']['repositoryUrls'] = [URL]
        with self.assertRaisesRegex(PlannerError, '不能补造'):
            Planner._parse(json.dumps(plan), plan['userQuery'])

    def test_planner_preserves_user_supplied_repository(self):
        plan = json.loads((ROOT/'examples/repository_plan_v02.json').read_text(encoding='utf-8'))
        self.assertEqual(Planner._parse(json.dumps(plan), plan['userQuery']), plan)

    def test_planner_cannot_hide_invented_repository_in_paper_ref(self):
        plan = fixture()
        plan['tasks'][1]['input']['papers'] = [{'url':'https://arxiv.org/abs/1706.03762','repositoryUrls':[URL]}]
        with self.assertRaisesRegex(PlannerError, '不能补造'):
            Planner._parse(json.dumps(plan), plan['userQuery'])

    def test_missing_repo_fails_before_executor_and_preserves_other_results(self):
        class NoRepository(LiteratureMockAgent):
            async def execute(self, payload, deps):
                result = await super().execute(payload, deps)
                result['items'][0]['data'].pop('repositoryUrls')
                return result
        class MustNotRun:
            async def execute(self, payload, deps):
                raise AssertionError('仓库地址缺失时不应执行')
        agents = [a for a in build_mock_agents() if a.agent_id == 'mock-paper_read']
        agents += [AgentHandle('lit', ('literature.search',), NoRepository()),
                   AgentHandle('repo', ('repository.analyze',), MustNotRun())]
        run = asyncio.run(LocalOrchestrator(MockAgentResolver(agents)).run_plan(fixture()))
        self.assertEqual([r['subtaskId'] for r in run['results']], ['S1','S2'])
        self.assertEqual(run['executionRecords'][2]['error']['code'], 'INPUT_INSUFFICIENT')
        self.assertIn('缺少仓库地址', run['executionRecords'][2]['error']['message'])

    def test_explicit_repository_is_not_expanded_by_upstream(self):
        plan = fixture()
        task = plan['tasks'][2]
        task['input']['repositoryUrls'] = [URL]
        result = asyncio.run(LiteratureMockAgent().execute(plan['tasks'][0], {}))
        bound = bind_task_input(task, {'S1':result})
        self.assertEqual(bound['input']['repositoryUrls'], [URL])

    def test_repository_locator_is_strict(self):
        for url in [URL, URL+'.git', URL+'/']:
            self.assertTrue(github_repository_url(url))
        for url in ['https://github.com', 'https://github.com/u', 'https://github.com/u/r/tree/main',
                    'http://github.com/u/r', 'https://github.com.evil/u/r', 'https://user@github.com/u/r',
                    'https://github.com/u/r?token=x', 'https://github.com/u/..', 'file:///tmp/r', None]:
            self.assertFalse(github_repository_url(url), url)

    def test_search_or_execution_permission_is_rejected(self):
        for field, value in [('allowRepositorySearch',True), ('allowRepositorySearch',1), ('executeCode',True)]:
            plan=fixture(); plan['tasks'][2]['constraints'][field]=value
            with self.assertRaises(ValueError): validate_plan(plan)

    def test_repository_flags_must_be_explicit(self):
        for field in ['allowRepositorySearch','executeCode']:
            plan=fixture(); del plan['tasks'][2]['constraints'][field]
            with self.assertRaises(ValueError): validate_plan(plan)

    def test_upstream_placeholder_allowed_only_until_dispatch(self):
        task=fixture()['tasks'][2]
        validate_task_input(task)
        with self.assertRaises(InputBindingError): validate_task_input(task, dispatch=True)
        task['dependsOn']=[]
        with self.assertRaises(InputBindingError): validate_task_input(task)

    def test_old_format_not_accepted_as_current_plan(self):
        with self.assertRaises(ValueError): validate_plan(fixture(True))
        plan=fixture(); task=plan['tasks'][0]; task['requiredCapability']=task.pop('requiredSkill')
        with self.assertRaises(ValueError): validate_plan(plan)

    def test_legacy_search_only_migration_does_not_mutate_source(self):
        old=fixture(True); old['tasks']=old['tasks'][:1]; original=deepcopy(old)
        new,notes=migrate_plan(old)
        self.assertEqual(old, original)
        self.assertEqual(new['schemaVersion'],'0.2')
        self.assertNotIn('requiredCapability',new['tasks'][0])
        self.assertTrue(notes)
        validate_plan(new)

    def test_legacy_repo_without_address_is_blocked(self):
        with self.assertRaisesRegex(MigrationError,'缺少仓库地址'): migrate_plan(fixture(True))

    def test_legacy_repo_with_address_migrates_with_explanation(self):
        old=fixture(True); old['tasks'][2]['input']['repositoryUrls']=[URL]
        old['tasks'][2]['constraints']['allowRepositorySearch']=True
        new,notes=migrate_plan(old)
        self.assertEqual(new['tasks'][2]['taskType'],'repository_analyze')
        self.assertFalse(new['tasks'][2]['constraints']['allowRepositorySearch'])
        self.assertTrue(any('仅分析' in note for note in notes))

    def test_legacy_conflicting_skill_is_rejected(self):
        old=fixture(True); old['tasks'][0]['requiredSkill']='repository.analyze'
        with self.assertRaisesRegex(MigrationError,'冲突'): migrate_plan(old)

    def test_legacy_equal_dual_skill_is_normalized(self):
        old=fixture(True); old['tasks']=old['tasks'][:1]
        old['tasks'][0]['requiredSkill']=old['tasks'][0]['requiredCapability']
        new,_=migrate_plan(old)
        self.assertNotIn('requiredCapability',new['tasks'][0])

    def test_bad_legacy_dependencies_are_rejected(self):
        old=fixture(True); old['tasks'][0]['dependsOn']=['S9']
        with self.assertRaisesRegex(MigrationError,'依赖'): migrate_plan(old)

    def test_unknown_version_and_mixed_versions_are_rejected(self):
        old=fixture(True); old['tasks'][0]['schemaVersion']='0.2'
        with self.assertRaises(ValueError): migrate_plan(old)
        old['schemaVersion']='9'
        with self.assertRaises(ValueError): migrate_plan(old)

    def test_current_plan_copy_is_idempotent(self):
        original=fixture(); new,notes=migrate_plan(original)
        self.assertEqual(new,original)
        self.assertIsNot(new,original)
        self.assertEqual(notes,[])
