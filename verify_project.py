"""一次性验收本地可独立完成的功能，并保存真实测试记录（不是 AIP 证明）。"""

import asyncio
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aggregator import render_report
from export_schemas import build_schemas
from run_demo import run_example
from tests.http_server import local_server


ROOT = Path(__file__).resolve().parent


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def request(base_url, route, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(base_url + route, data=body, headers={"Content-Type": "application/json"})
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        return {"httpStatus": response.status, "body": json.loads(response.read().decode("utf-8"))}


def main():
    output = ROOT / "outputs" / datetime.now().strftime("acceptance-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    tests = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    test_log = tests.stdout + tests.stderr
    (output / "test_log.txt").write_text(test_log, encoding="utf-8")
    if tests.returncode != 0:
        raise RuntimeError("自动测试未通过，请查看 " + str(output / "test_log.txt"))
    match = re.search(r"Ran (\d+) tests", test_log)
    test_count = int(match.group(1)) if match else None
    skipped_match = re.search(r"OK \(skipped=(\d+)\)", test_log)
    skipped_count = int(skipped_match.group(1)) if skipped_match else 0
    passed_count = test_count - skipped_count if test_count is not None else None

    for name, expected in build_schemas().items():
        actual = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        if actual != expected:
            raise RuntimeError("Schema 已过期，请运行 export_schemas.py 后重新验收")

    scenarios = []
    for name, case, fail_task, expected_successes in (
        ("normal", 1, None, 3), ("read_failure", 1, "S2", 2),
        ("search_failure", 1, "S1", 0), ("single_paper", 2, None, 1),
        ("clarification", 3, None, 0),
    ):
        plan, run = asyncio.run(run_example(case, fail_task))
        if len(run["results"]) != expected_successes:
            raise RuntimeError("模拟场景验收失败：" + name)
        directory = output / name
        directory.mkdir()
        save_json(directory / "plan.json", plan)
        save_json(directory / "research_result.json", run)
        save_json(directory / "run_log.json", run["executionRecords"])
        (directory / "report.md").write_text(render_report(run), encoding="utf-8")
        scenarios.append({"name": name, "successes": len(run["results"]),
                          "needsClarification": run["needsClarification"], "passed": True})

    with local_server() as base_url:
        health = request(base_url, "/health")
        cases = request(base_url, "/demo/cases")
        planned = request(base_url, "/plan", {"userQuery": cases["body"][0]["userQuery"]})
        unknown = request(base_url, "/plan", {"userQuery": "未收录的新研究问题"})
        blank = request(base_url, "/plan", {"userQuery": " "})
        openapi = request(base_url, "/openapi.json")
    observed = {"health": health, "cases": cases, "plan": planned,
                "unknownQuery": unknown, "invalidInput": blank, "openapi": openapi}
    expected_statuses = {"health": 200, "cases": 200, "plan": 200,
                         "unknownQuery": 409, "invalidInput": 422, "openapi": 200}
    for name, response in observed.items():
        if response["httpStatus"] != expected_statuses[name]:
            raise RuntimeError("HTTP 验收失败：" + name)
    save_json(output / "http_evidence.json", observed)
    environment = {"python": sys.version, "executable": sys.executable,
                   "packages": {name: importlib.metadata.version(name) for name in
                                ("pydantic", "fastapi", "uvicorn", "jsonschema")}}
    save_json(output / "environment.json", environment)
    result = {"verifiedAt": datetime.now().astimezone().isoformat(),
              "demoOnly": True, "plannerMode": "fixture", "executionMode": "local-mock",
              "testsPassed": passed_count, "testsSkipped": skipped_count, "testsRun": test_count,
              "schemasCurrent": True, "scenarios": scenarios,
              "httpChecksPassed": True, "realModelConnected": False,
              "realAgentsConnected": False, "aipConnected": False}
    save_json(output / "acceptance.json", result)
    (output / "SUMMARY.md").write_text(
        "# ResearchMesh 本地验收记录\n\n"
        f"验收时间：{result['verifiedAt']}\n\n"
        f"自动测试：{passed_count} 项通过，{skipped_count} 项跳过。4 份 Schema 与当前模型一致。\n\n"
        "HTTP 验收：health/cases/plan/openapi 返回 200，新问题返回 409，空输入返回 422。\n\n"
        "模拟场景：正常 3 项成功；阅读失败保留 2 项；检索失败则 0 项成功并跳过下游；"
        "单篇阅读 1 项成功；模糊需求只追问。\n\n"
        "证据：test_log.txt、http_evidence.json、environment.json、acceptance.json，"
        "各场景目录内的 plan/result/log/report 文件。\n\n"
        "边界：以上只验证预写规划和同进程 Mock 执行；不证明真实模型、论文数据、"
        "自动平台发现、AIP 通信或赛事互联已经完成。临时测试服务在验收后关闭。\n",
        encoding="utf-8",
    )
    print(f"验收通过：{passed_count} 项自动测试（跳过 {skipped_count} 项） + 5 个模拟场景 + 本地 HTTP 检查。")
    print("记录已保存：" + str(output))


if __name__ == "__main__":
    main()
