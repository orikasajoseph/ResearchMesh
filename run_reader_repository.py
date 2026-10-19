"""Reuse verified real Reader result -> new official local AIP Repository comparison.

No new Planner/search/PDF/Reader calls. Static is the default; explicit enhanced
mode permits bounded model calls only in the Repository stage.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from reader_repository_handoff import load_reader_source, verify_manifest, build_handoff
from repository_adapter import RepositoryAdapterError
from run_aip_repository import main as repository_main
from run_aip_business import save_json

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reader-source', type=Path, required=True, help='此前成功的 AIP Reader 目录或检索→阅读父目录')
    parser.add_argument('--reader-cache', type=Path, default=ROOT / 'outputs/pdf-cache',
                        help='此前已校验 PDF 的缓存根目录；仅复核文件与指纹，不下载或阅读')
    parser.add_argument('--repository', required=True, help='明确的 GitHub 仓库 URL；不自动搜索或猜测')
    parser.add_argument('--wrapper-root', type=Path, default=ROOT / 'agents/repository')
    parser.add_argument('--repository-python', type=Path, default=ROOT / '.venv-repository/Scripts/python.exe')
    parser.add_argument('--analysis-mode', choices=('static', 'enhanced'), default='static')
    parser.add_argument('--env-file', type=Path, help='仅 enhanced 执行时读取 DeepSeek 配置')
    parser.add_argument('--execute', action='store_true', help='static 只做静态分析；enhanced 可能产生模型费用')
    args = parser.parse_args(argv)
    output = None
    try:
        checkpoint = load_reader_source(args.reader_source, cache_dir=args.reader_cache.resolve())
        handoff = build_handoff(checkpoint, args.repository)
        if args.analysis_mode == 'enhanced':
            handoff['plan']['userQuery'] = '复用已完成论文阅读，对照指定仓库 ' + args.repository + '；仅 Repository 开启显式模型增强，不重新检索或阅读。'
        output = ROOT / 'outputs' / (datetime.now().strftime('aip-research-handoff-%Y%m%d-%H%M%S-') + uuid4().hex[:8])
        output.mkdir(parents=True, exist_ok=False)
        save_json(output / 'reader_checkpoint.json', checkpoint)
        save_json(output / 'handoff.json', handoff)
        save_json(output / 'repository_plan.json', handoff['plan'])
        verify_manifest(checkpoint['sourceManifest'])
        command = ['--plan', str(output / 'repository_plan.json'), '--record-dir', str(output / 'repository'),
                   '--wrapper-root', str(args.wrapper_root.resolve()), '--repository-python', str(args.repository_python.resolve()),
                   '--analysis-mode', args.analysis_mode]
        if args.env_file: command += ['--env-file', str(args.env_file.resolve())]
        if args.execute: command.append('--execute')
        print('【复用全文笔记 → 本地 AIP 仓库对照】不重新检索/阅读；DeepSeek 关闭。' if args.analysis_mode == 'static'
              else '【复用全文笔记 → 本地 AIP 仓库增强】不重新检索/阅读；DeepSeek 可能计费。', flush=True)
        code = repository_main(command)
        verify_manifest(checkpoint['sourceManifest'])
        if not args.execute:
            print('仅完成预检；Reader 未重跑，Repository 未执行。记录：', output)
            return code
        acceptance_path = output / 'repository/acceptance.json'
        child = json.loads(acceptance_path.read_text(encoding='utf-8')) if acceptance_path.is_file() else {}
        result_path = output / 'repository/outcome.json'
        outcome = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
        context_done = child.get('paperContextCompared') is True if args.analysis_mode == 'static' else child.get('paperContextAnalyzed') is True
        passed = code == 0 and child.get('passed') is True and context_done
        acceptance = {'passed': passed, 'scope': ('REUSED_REAL_AIP_READER_TO_NEW_LOCAL_AIP_STATIC_REPOSITORY' if args.analysis_mode == 'static'
                                                else 'REUSED_REAL_AIP_READER_TO_NEW_LOCAL_AIP_ENHANCED_REPOSITORY'),
            'readerReused': True, 'readerSourceUnchanged': True,
            'previousReaderAipTaskId': checkpoint['readerRecord']['aipTaskId'],
            'newRepositoryProtocolCompleted': child.get('protocolCompleted') is True,
            'paperContextCompared': child.get('paperContextCompared') is True,
            'paperContextAnalyzed': child.get('paperContextAnalyzed') is True,
            'enhancementSucceeded': child.get('enhancementSucceeded') is True,
            'newPlannerCalls': 0, 'newSearchCalls': 0, 'newReaderCalls': 0,
            'newModelCalls': child.get('modelCalls') if args.analysis_mode == 'enhanced' else 0,
            'freshThreeAgentEndToEnd': False, 'semanticReproductionAnalysis': False,
            'reproductionVerified': False, 'platformDiscoveryUsed': False}
        save_json(output / 'acceptance.json', acceptance)
        save_json(output / 'combined_run.json', {'acceptance': acceptance, 'previousReaderRecord': checkpoint['readerRecord'],
                                               'newRepositoryOutcome': outcome})
        header = ['# ResearchMesh 论文阅读结果 → 仓库静态对照', '',
            '> Reader 是此前真实完成的结果，本轮只重新执行 Repository 本地 AIP。不是三个 Agent 一次全新运行。',
            '> 关闭 DeepSeek；对照只查文本/标识符线索，不评定复现难度或验证复现成功。', '',
            '本轮验收：' + str(passed), '原 Reader AIP taskId：`' + checkpoint['readerRecord']['aipTaskId'] + '`',
            '原 Reader 目录：' + checkpoint['readerSource'], '', '## 已复用的全文笔记', '']
        if args.analysis_mode == 'enhanced':
            header[0] = '# ResearchMesh 论文阅读结果 → 仓库证据增强分析'
            header[3] = '> 显式开启 DeepSeek；论文/代码关联属于证据约束推断，仍需人工核验；未执行代码或验证复现。'
        for item in checkpoint['readerRecord']['result']['items']:
            note = item['data']
            header += ['论文：' + str(note['source'].get('title') or note['source']['paperId']), '',
                       '方法：' + note['method'], '', '发现：' + note['findings'], '', '局限：' + note['limitations'], '']
        child_report = output / 'repository/report.md'
        (output / 'report.md').write_text('\n'.join(header) + '\n\n' +
            (child_report.read_text(encoding='utf-8') if child_report.is_file() else '仓库阶段未完成；请查看阶段记录。\n'), encoding='utf-8')
        print('S2 旧结果 → S3 新 AIP：', '通过' if passed else '未完成')
        print('汇总报告：', output / 'report.md')
        return 0 if passed else 1
    except (Exception, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, RepositoryAdapterError) else type(error).__name__
        if output: save_json(output / 'failure.json', {'code': code, 'newModelCalls': None if args.analysis_mode == 'enhanced' else 0,
                                                       'modelExecutionUncertain': bool(args.execute and args.analysis_mode == 'enhanced')})
        print('未完成：', code, '；记录：', output or '源记录检查未通过，没有启动 Agent')
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())
