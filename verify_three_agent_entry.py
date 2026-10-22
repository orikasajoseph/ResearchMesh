"""Free unified-entry regression: no keys, model, external HTTP, PDF or clone.

Uses the real Planner/parser, business adapters and official SDK via in-memory
ASGI. Scientific providers are synthetic; this is NOT a live acceptance record.
"""
from contextlib import redirect_stdout
from datetime import datetime
import io
from pathlib import Path
import unittest

from run_aip_business import save_json

ROOT = Path(__file__).resolve().parent


def main():
    output = ROOT / "outputs" / datetime.now().strftime("three-agent-offline-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_three_agent_entry")
    log = io.StringIO()
    with redirect_stdout(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    (output / "test_log.txt").write_text(log.getvalue(), encoding="utf-8")
    value = {"scope": "OFFLINE_UNIFIED_ENTRY_REAL_PLANNER_AND_AIP_SDK_SYNTHETIC_BACKENDS",
             "passed": result.wasSuccessful(), "testsRun": result.testsRun, "skipped": len(result.skipped),
             "failures": len(result.failures), "errors": len(result.errors),
             "externalApiCalls": 0, "paidModelCalls": 0, "realPdfDownloads": 0, "realRepositoryClones": 0,
             "officialAipTransport": "ASGI_IN_MEMORY_NO_NETWORK",
             "scientificProviders": "SYNTHETIC_TEST_ONLY", "realFreshThreeAgentRunVerified": False,
             "platformDiscoveryVerified": False}
    save_json(output / "acceptance.json", value)
    (output / "SUMMARY.md").write_text(
        "# 三智能体统一入口免费测试\n\n"
        f"通过：{value['passed']}；运行 {result.testsRun} 项，跳过 {len(result.skipped)} 项。\n\n"
        "覆盖：新问题规划、检索→单篇阅读→单仓库绑定、官方 AIP 消息、汇总报告、地址核验、"
        "中断/失败保留、续跑防重复、检查点防篡改、增强成功及降级。\n\n"
        "费用与网络：0 次真实模型请求、0 次外部 HTTP、0 次真实 PDF 下载、0 次真实 clone。"
        "科研后端均为测试替身；不是收费真实整链或正式梧桐验收。\n",
        encoding="utf-8")
    print(f"统一入口离线测试：{'通过' if result.wasSuccessful() else '未通过'}；{result.testsRun} 项，收费调用 0 次。")
    print("记录：", output)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
