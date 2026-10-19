"""Explicit repository -> existing Leader/Resolver -> official local AIP -> CodeWiki.

Default is offline preflight. --execute alone permits static analysis only.
--analysis-mode enhanced --execute explicitly permits bounded LLM requests.
Neither mode searches for repositories or executes target commands.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
from acps_sdk.aip import AipRpcClient
from aip_executor import AipAgentExecutor
from aip_lifecycle import AipTaskRunner, sdk_sender
from aip_orchestrator import AipOrchestrator
from aip_repository_partner import static_worker_env, repository_worker_env
from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input
from planner_contract import validate_plan
from repository_adapter import to_repository_request, RepositoryAdapterError
from repository_paper_context import prepare_repository_task, comparison_valid
from aip_record_paths import require_record_directory
from resolver.base import AgentHandle
from resolver.local import LocalAipResolver
from run_aip_business import save_json
from run_aip_literature import live_partner

ROOT = Path(__file__).resolve().parent


def build_plan(url):
    plan = json.loads((ROOT / 'examples/repository_s3_week3.json').read_text(encoding='utf-8'))
    plan['userQuery'] = '仅静态分析指定仓库 ' + url + '；不执行代码，不调用模型。'
    plan['tasks'][0]['input']['repositoryUrls'] = [url]
    validate_plan(plan)
    to_repository_request(plan['tasks'][0])
    return plan


def selected_plan(source):
    validate_plan(source)
    if source['needsClarification'] or len(source['tasks']) != 1:
        raise RepositoryAdapterError('ONE_INDEPENDENT_REPOSITORY_TASK_REQUIRED')
    task = source['tasks'][0]
    if task['taskType'] != 'repository_analyze' or task['dependsOn']:
        raise RepositoryAdapterError('ONE_INDEPENDENT_REPOSITORY_TASK_REQUIRED')
    prepare_repository_task(task)
    return deepcopy(source)


async def invoke_repository(plan, url, aic, *, transport=None, timeout_seconds=195):
    parsed = httpx.URL(url)
    if (parsed.scheme != 'http' or parsed.host != '127.0.0.1' or parsed.path != '/rpc'
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise RepositoryAdapterError('LOOPBACK_RPC_REQUIRED')
    messages = []
    class TracedClient(AipRpcClient):
        async def _send_request(self, command):
            response = await super()._send_request(command)
            messages.append({'command': command.model_dump(mode='json', exclude_none=True),
                             'response': response.model_dump(mode='json', exclude_none=True)})
            return response
    leader = 'local-repository-leader'
    client = TracedClient(url, leader, identity_binding_enabled=False,
                         transport=transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False))
    try:
        runner = AipTaskRunner(sdk_sender(client), leader, aic, timeout_seconds=timeout_seconds, poll_seconds=.1)
        executor = AipAgentExecutor(runner)
        resolver = LocalAipResolver([AgentHandle(aic, ('repository.analyze',), executor)])
        outcome = await AipOrchestrator(resolver).run_plan(selected_plan(plan))
        # Local registry is not official ADP and deterministic task construction is not LLM planning.
        outcome.update(executionMode='official-local-aip-explicit-repository', aipMessages=messages,
                       resolverScope=resolver.scope, plannerCalled=False)
        return outcome
    finally:
        await client.close()


def assess(outcome, backend, payload):
    records = outcome.get('executionRecords', [])
    protocol = len(records) == 1 and records[0].get('succeeded') is True and records[0].get('aipState') == 'completed'
    result = (outcome.get('results') or [None])[0]
    diagnostic = (backend or {}).get('diagnostics', {})
    matched = bool(protocol and backend and backend.get('phase') == 'completed' and backend.get('error') is None
                   and backend.get('task') == payload and result == backend.get('result'))
    data = result['items'][0]['data'] if matched else {}
    static = data.get('diagnostics', {})
    valid = bool(matched and backend.get('scope') == 'ISOLATED_CODEWIKI_STATIC_NO_LLM'
                 and data.get('isMock') is False and data.get('evidence')
                 and static.get('provider') == 'codewiki-static' and static.get('counts', {}).get('componentCount', 0) > 0
                 and static.get('metrics', {}).get('llmEnrichmentMs') == 0
                 and diagnostic.get('llmEnabled') is False and diagnostic.get('modelCalls') == 0
                 and diagnostic.get('pythonNetworkAttemptsBlocked') == 0
                 and diagnostic.get('targetCodeExecuted') is False and diagnostic.get('targetDependenciesInstalled') is False)
    needs_context = bool(payload['input'].get('papers') or payload['input'].get('paperNotes') or payload['constraints'].get('requirePaperNotes'))
    context_compared = bool(matched and diagnostic.get('paperContextCompared') is True and comparison_valid(result, payload)) if needs_context else False
    if needs_context:
        valid = valid and context_compared
    return {'passed': valid, 'protocolCompleted': bool(protocol), 'businessReady': valid,
            'scope': 'LOCAL_AIP_REAL_REPOSITORY_STATIC_ONLY_NO_LLM', 'backendResultMatchesAipProduct': matched,
            'modelCalled': False, 'plannerCalled': False, 'platformDiscoveryUsed': False, 'tlsIdentityVerified': False,
            'targetCodeExecuted': False, 'targetDependenciesInstalled': False,
            'reproductionVerified': False, 'paperContextAnalyzed': False,
            'paperContextCompared': context_compared, 'semanticReproductionAnalysis': False,
            'returnedRepositoryCount': 1 if valid else 0}


def render_report(outcome, acceptance):
    lines = ['# ResearchMesh 指定仓库静态分析报告', '',
             '> 真实 Git clone + CodeWiki 静态分析，经 Leader / 本地能力匹配 / 官方 AIP 返回。',
             '> DeepSeek 增强关闭；没有执行目标代码、安装目标依赖或验证复现。没有使用梧桐/ADP。', '',
             '协议完成：' + str(acceptance['protocolCompleted']), '静态业务验收：' + str(acceptance['businessReady']), '']
    for result in outcome.get('results', []):
        for item in result['items']:
            data = item['data']; identity = data['repository']; counts = data['diagnostics']['counts']
            lines += ['## 仓库与分析范围', '', '仓库：' + identity['url'], 'Commit：`' + identity['commitHash'] + '`',
                      f"组件 {counts['componentCount']}；依赖边 {counts['dependencyEdgeCount']}；证据 {counts['evidenceCount']}。", '',
                      '## 文件与组件', '']
            for file in data['structure'][:80]:
                lines += ['- `' + file['filePath'] + '`：' + ', '.join(c['name'] for c in file['components'])]
            if len(data['structure']) > 80: lines += ['文件较多，仅展示前 80 项；完整数据见 JSON。']
            lines += ['', '## 静态依赖边', '', '> 表示解析到的组件依赖/调用关系，不等于完整的软件包安装清单。', '']
            lines += ['- `' + d['source'] + '` → `' + d['target'] + '`' for d in data['dependencies'][:100]]
            if len(data['dependencies']) > 100: lines += ['依赖较多，仅展示前 100 条；完整数据见 JSON。']
            lines += ['', '## 声明的入口与运行命令（未执行）', '']
            def short(value):
                return ' '.join(str(value).split()).replace('`', "'")[:200]
            lines += ['- ' + short(e['name']) + '（' + short(e['entrypointType']) + '）：`' + short(e['target']) +
                      '`；来源：' + short(e['source']) for e in data['entrypoints']]
            lines += ['- `' + short(c['command']) + '`，来源：' + short(c['source']) for c in data['runCommands']]
            if not data['entrypoints'] and not data['runCommands']:
                lines += ['未提取到声明的入口或命令；不代表该仓库不能运行。']
            else:
                lines += ['', '长声明仅显示摘要；完整声明与原始片段保存在 outcome.json / backend_outcome.json 中。']
            lines += ['', '## 源码证据', '', '> 链接由 clone 的 commit 与文件位置构造，未逐条联网核验。', '']
            for evidence in data['evidence'][:80]:
                lines += ['- [' + evidence['sourceId'] + ' · ' + evidence['locator'] + '](' + evidence['url'] + ')：' + evidence['supports']]
            if len(data['evidence']) > 80: lines += ['证据较多，仅展示前 80 条；完整数据见 JSON。']
            comparison = data.get('paperComparison')
            if comparison:
                lines += ['', '## 论文笔记 → 仓库证据对照（无模型）', '',
                          '> 仅定位文本/标识符线索，不证明算法实现、性能结论、实验设置等价或复现成功。未匹配也不证明不存在。', '']
                for paper in comparison['papers']:
                    lines += ['### ' + str(paper['source'].get('title') or paper['source']['paperId']), '',
                              '输入笔记指纹：`' + paper['noteSha256'] + '`', '']
                    identifiers = paper['repositoryIdentifierMentions']
                    lines += ['仓库摘录中出现该论文标识符：' + (', '.join(x['identifier'] + ' [' + x['repositoryEvidenceId'] + ']' for x in identifiers) if identifiers else '未找到；关联未核验。'), '']
                    for row in paper['rows']:
                        lines += ['#### ' + {'method': '论文方法', 'findings': '论文发现', 'limitations': '论文局限'}[row['field']], '', row['paperText'], '']
                        evidence_urls = {e['sourceId']: e['url'] for e in data['evidence']}
                        lines += ['- 仓库线索 [' + x['repositoryEvidenceId'] + '](' + evidence_urls[x['repositoryEvidenceId']] + ')：' + ', '.join(x['terms']) for x in row['matches']]
                        if not row['matches']: lines += ['未定位到词面线索，需人工核验。']
                        lines += ['']
                    lines += ['原论文证据（沿用已有 Reader，结论仍待人工核验）：', '']
                    lines += ['- [' + e['locator'] + '](' + e['url'] + ')' for e in paper['paperEvidence']]
            lines += ['', '## 边界与警告', '', '模型概述与复现难度推断未生成（本轮关闭 LLM）。许可证及论文/仓库关联未核验。', '']
            lines += ['- ' + str(w) for w in result['warnings']]
    if not acceptance['passed']:
        lines += ['', '## 未完成', '', '请查看 outcome.json、backend_outcome.json 和阶段日志；本报告不是成功分析证明。']
    return '\n'.join(lines) + '\n'


def assess_enhanced(outcome, backend, payload):
    from repository_enhancement_contract import validate_enhancement_evidence
    records = outcome.get('executionRecords', [])
    protocol = bool(len(records) == 1 and records[0].get('succeeded') is True
                    and records[0].get('aipState') == 'completed')
    result = (outcome.get('results') or [None])[0]
    matched = bool(protocol and backend and backend.get('phase') == 'completed'
                   and backend.get('error') is None and backend.get('task') == payload
                   and result == backend.get('result'))
    diagnostic = (backend or {}).get('diagnostics', {})
    data = result['items'][0]['data'] if matched and result.get('items') else {}
    static_ready = bool(matched and backend.get('scope') == 'ISOLATED_CODEWIKI_WITH_EXPLICIT_LLM_ENHANCEMENT'
                        and data.get('isMock') is False and data.get('evidence')
                        and data.get('diagnostics', {}).get('provider') == 'codewiki-static'
                        and data.get('diagnostics', {}).get('counts', {}).get('componentCount', 0) > 0
                        and diagnostic.get('llmEnabled') is True
                        and type(diagnostic.get('modelCalls')) is int and 0 <= diagnostic['modelCalls'] <= 2
                        and diagnostic.get('pythonNetworkAttemptsBlocked') == 0
                        and diagnostic.get('targetCodeExecuted') is False
                        and diagnostic.get('targetDependenciesInstalled') is False)
    evidence_valid = False
    if static_ready:
        try:
            validate_enhancement_evidence(data, payload)
            evidence_valid = True
        except (ValueError, TypeError, KeyError):
            pass
    status = diagnostic.get('enrichmentStatus', 'not_started')
    enhancement = bool(static_ready and evidence_valid and status == 'completed'
                       and data.get('enrichment', {}).get('status') == status
                       and diagnostic.get('modelCalls', 0) >= 1
                       and diagnostic.get('successfulModelResponses', 0) >= 1
                       and (data.get('overview') or data.get('reproductionBarriers') or data.get('paperCodeRelations'))
                       and (not payload['input'].get('paperNotes') or data.get('paperCodeRelations'))
                       and 'LLM_ENRICHMENT_UNAVAILABLE' not in result['warnings'])
    return {'passed': enhancement, 'protocolCompleted': protocol, 'businessReady': bool(static_ready and evidence_valid),
            'staticBusinessReady': static_ready, 'enhancementSucceeded': enhancement, 'enrichmentStatus': status,
            'scope': 'LOCAL_AIP_REPOSITORY_EXPLICIT_ENHANCEMENT', 'backendResultMatchesAipProduct': matched,
            'modelCalled': diagnostic.get('modelCalls', 0) > 0, 'modelCalls': diagnostic.get('modelCalls', 0),
            'modelUsage': diagnostic.get('modelUsage', []), 'usageMayExcludeFailedRequests': True,
            'plannerCalled': False, 'platformDiscoveryUsed': False, 'tlsIdentityVerified': False,
            'targetCodeExecuted': False, 'targetDependenciesInstalled': False, 'reproductionVerified': False,
            'paperContextAnalyzed': bool(enhancement and payload['input'].get('paperNotes')),
            'paperContextCompared': False, 'semanticReproductionAnalysis': False,
            'returnedRepositoryCount': 1 if static_ready else 0}


def render_enhanced_report(outcome, acceptance):
    report = render_report(outcome, dict(acceptance, passed=acceptance['businessReady']))
    report = report.replace('指定仓库静态分析报告', '指定仓库分析报告（显式模型增强）')
    report = report.replace('DeepSeek 增强关闭；', '已请求 DeepSeek 增强；')
    report = report.replace('模型概述与复现难度推断未生成（本轮关闭 LLM）。',
                            '模型分析是证据约束下的推断，不代表执行或复现验证。')
    lines = [report, '## 可选增强状态', '', '增强状态：' + acceptance['enrichmentStatus'],
             '增强完整验收：' + str(acceptance['enhancementSucceeded']),
             '模型请求尝试数：' + str(acceptance['modelCalls']),
             '失败或超时请求也可能计费；token 仅统计收到的成功响应，未计算费用。', '']
    for result in outcome.get('results', []):
        for item in result['items']:
            data = item['data']
            lines += ['### 模型概述', '', (data.get('overview') or {}).get('text') or '未生成有效概述。', '',
                      '### 复现障碍推断（未实际复现）', '']
            for barrier in data.get('reproductionBarriers', []):
                lines += ['- ' + barrier['issue'] + '；代码证据：' + ', '.join(barrier['evidenceIds'])]
            if not data.get('reproductionBarriers'):
                lines += ['未生成有证据支持的障碍；不能据此认定容易复现。']
            lines += ['', '### 论文与代码关联（待人工核验）', '']
            for relation in data.get('paperCodeRelations', []):
                lines += ['- ' + relation['claim'],
                          '  论文证据：' + ', '.join(relation['paperEvidenceIds']) +
                          '；代码证据：' + ', '.join(relation['codeEvidenceIds'])]
            if not data.get('paperCodeRelations'):
                lines += ['未生成有效的双侧证据关联；没有论文输入时不生成关联。']
            lines += ['', '### 沿用的论文证据', '']
            for e in data.get('paperEvidence', []):
                lines += ['- [' + e['evidenceId'] + ' · ' + e['locator'] + '](' + e['url'] + ')：' + e['supports']]
    if not acceptance['enhancementSucceeded']:
        lines += ['', '> 增强未完成。已保留可用静态结果；不要将本报告当作增强分析成功证明。']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--repository', help='一个明确的 GitHub 仓库 HTTPS URL；不提供则交互输入')
    choice.add_argument('--plan', type=Path, help='已保存的单独 Repository 任务计划；不会重新调用模型')
    parser.add_argument('--wrapper-root', type=Path, default=ROOT / 'agents/repository')
    parser.add_argument('--repository-python', type=Path, default=ROOT / '.venv-repository/Scripts/python.exe')
    parser.add_argument('--analysis-mode', choices=('static', 'enhanced'), default='static', help='默认 static 零模型；enhanced + --execute 才允许模型计费')
    parser.add_argument('--env-file', type=Path, help='仅 enhanced 执行时读取其中的 DeepSeek 配置；不复制、不打印密钥')
    parser.add_argument('--execute', action='store_true', help='允许执行所选模式；enhanced 模式可能计费')
    parser.add_argument('--record-dir', type=Path, help='新的 outputs 内目录；不覆盖已有记录')
    args = parser.parse_args(argv)
    output = None
    try:
        if args.analysis_mode == 'static' and args.env_file:
            raise ValueError('ENV_FILE_REQUIRES_ENHANCED_MODE')
        if args.env_file and not args.env_file.is_file():
            raise ValueError('ENHANCED_ENV_FILE_MISSING')
        source = json.loads(args.plan.read_text(encoding='utf-8-sig')) if args.plan else build_plan(
            args.repository or input('请输入 GitHub 仓库链接：').strip())
        if args.analysis_mode == 'enhanced' and not args.plan:
            source['userQuery'] = '静态分析指定仓库并使用证据约束的模型增强；不执行目标代码。'
            source['tasks'][0]['goal'] = source['userQuery']
        plan = selected_plan(source)
        bound_task = bind_task_input(plan['tasks'][0], {})
        payload = {k: deepcopy(bound_task[k]) for k in BUSINESS_TASK_FIELDS}
        if importlib.metadata.version('acps-sdk') != '2.2.0':
            raise ValueError('SDK_220_REQUIRED')
        python, wrapper = args.repository_python.resolve(), args.wrapper_root.resolve()
        if not python.is_file() or not (wrapper / 'researchmesh_repository/__init__.py').is_file():
            raise ValueError('REPOSITORY_RUNTIME_OR_WRAPPER_MISSING')
        output = args.record_dir.resolve() if args.record_dir else ROOT / 'outputs' / (datetime.now().strftime('aip-repository-%Y%m%d-%H%M%S-') + uuid4().hex[:8])
        output.mkdir(parents=True, exist_ok=False)
        require_record_directory(output, ROOT, 'repository')
        save_json(output / 'plan.json', plan); save_json(output / 'repository_input.json', payload)
        script = 'repository_aip_worker.py' if args.analysis_mode == 'static' else 'repository_enhanced_worker.py'
        command = [str(python), '-X', 'utf8', str(ROOT / script),
                   '--record-dir', str(output), '--wrapper-root', str(wrapper)]
        with (output / 'preflight.log').open('w', encoding='utf-8') as log:
            checked = subprocess.run(command, cwd=ROOT, env=repository_worker_env(args.analysis_mode), stdout=log, stderr=subprocess.STDOUT, timeout=60)
        if checked.returncode:
            raise ValueError('REPOSITORY_PREFLIGHT_FAILED')
        save_json(output / 'environment.json', {'python': str(python), 'wrapperRoot': str(wrapper),
            'executeRequested': args.execute, 'analysisMode': args.analysis_mode,
            'llmEnabled': args.analysis_mode == 'enhanced', 'sdkVersion': '2.2.0',
            'wrapperHashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wrapper / 'researchmesh_repository').glob('*.py')}})
        if not args.execute:
            print('预检通过；未 clone、未发送 AIP 任务、未调用模型。加 --execute 才执行。')
            print('记录：', output); return 0
        print('【真实本地 AIP + 指定仓库静态分析】不调用 DeepSeek，不运行目标代码。' if args.analysis_mode == 'static'
              else '【真实本地 AIP + 显式模型增强】最多 2 次 DeepSeek 请求，会计费；不运行目标代码。', flush=True)
        extra_env = {'RESEARCHMESH_REPOSITORY_PYTHON': str(python),
                     'RESEARCHMESH_REPOSITORY_ANALYSIS_MODE': args.analysis_mode,
                     'RESEARCHMESH_REPOSITORY_ENV_FILE': str(args.env_file.resolve()) if args.env_file else ''}
        with live_partner(wrapper, output, factory='aip_repository_partner:create_live_app', mode='local-repository-live',
                          extra_env=extra_env, aic_prefix='local-repository') as (url, aic):
            outcome = asyncio.run(invoke_repository(plan, url, aic, timeout_seconds=195 if args.analysis_mode == 'static' else 315))
        save_json(output / 'outcome.json', outcome)
        path = output / 'backend_outcome.json'
        backend = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
        acceptance = (assess if args.analysis_mode == 'static' else assess_enhanced)(outcome, backend, payload)
        acceptance['partnerStopped'] = True
        save_json(output / 'acceptance.json', acceptance)
        renderer = render_report if args.analysis_mode == 'static' else render_enhanced_report
        (output / 'report.md').write_text(renderer(outcome, acceptance), encoding='utf-8')
        print('protocolCompleted：', acceptance['protocolCompleted']); print('businessReady：', acceptance['businessReady'])
        if not acceptance['passed']:
            print('未完成：', (backend or {}).get('error') or [r.get('error') for r in outcome['executionRecords']])
        print('报告：', output / 'report.md')
        print('临时 Partner 已停止；DeepSeek 调用 0 次。' if args.analysis_mode == 'static' else
              '增强状态：' + acceptance['enrichmentStatus'] + '；模型请求尝试数：' + str(acceptance['modelCalls']))
        return 0 if acceptance['passed'] else 1
    except (Exception, KeyboardInterrupt) as error:
        code = getattr(error, 'code', None) or type(error).__name__
        # Fixed messages only; never raw provider/clone logs on console.
        if type(error) is ValueError and str(error) in {'SDK_220_REQUIRED', 'REPOSITORY_RUNTIME_OR_WRAPPER_MISSING', 'REPOSITORY_PREFLIGHT_FAILED', 'ENV_FILE_REQUIRES_ENHANCED_MODE', 'ENHANCED_ENV_FILE_MISSING'}:
            code = str(error)
        if output:
            save_json(output / 'failure.json', {'code': code, 'llmEnabled': args.analysis_mode == 'enhanced',
                                               'modelExecutionUncertain': bool(args.execute and args.analysis_mode == 'enhanced')})
        print('未完成：', code, '；记录：', output or '未创建（输入检查未通过）')
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())
