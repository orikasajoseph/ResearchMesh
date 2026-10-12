"""Saved AIP search -> bound single Reader -> local AIP. Default is free preflight.

Reader stays in its isolated runtime; --execute opts into possibly multiple paid
LLM requests. No automatic Reader retry, re-search, Planner or PDF download.
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

from aip_reader_inputs import prepare_reader_input, read_json
from reader_adapter import ReaderAdapterError, normalize_reader_result
from run_aip_literature import live_partner, invoke_business
from run_aip_business import save_json

ROOT = Path(__file__).resolve().parent
DEFAULT_READER_PYTHON = Path("C:/Users/orika/Documents/Codex/rmr-1011-70db/Scripts/python.exe")


def assess_reader_run(outcome, journal, payload):
    execution = outcome.get("execution")
    protocol = bool(execution and outcome.get("error") is None and execution["state"] == "completed")
    valid = False
    if (journal and journal.get("phase") == "completed" and journal.get("task") == payload
            and isinstance(journal.get("outcome"), dict) and journal["outcome"].get("error") is None):
        try:
            normalized = normalize_reader_result(payload, journal["outcome"]["result"])
            valid = bool(normalized["error"] is None and normalized["diagnostics"].get("analysisLevel") == "fulltext"
                         and protocol and normalized["result"] == execution["result"]
                         and execution.get("simulatedResult") is False
                         and journal.get("scope") == "LIVE_READER_LOCAL_PDF_WORKER"
                         and journal.get("modelExecutionAttemptedEver") is True)
        except (ValueError, KeyError, TypeError):
            valid = False
    reused = bool(journal and journal.get("readerResultReused") is True)
    return {"passed": valid, "protocolCompleted": protocol, "businessReady": valid,
            "scope": "LOCAL_AIP_SINGLE_READER_LIVE", "readerResultReused": reused,
            "readerModelExecutionAttemptedThisRun": journal.get("modelExecutionAttemptedThisRun") if journal else None,
            "modelRequestCount": None, "automaticReaderRetries": 0,
            "searchReused": True, "plannerCalled": False, "paperApiCalledThisRun": False,
            "pdfDownloadedThisRun": False, "platformDiscoveryUsed": False, "tlsIdentityVerified": False,
            "scientificClaimsHumanVerified": False}


def render_reader_report(outcome, acceptance):
    lines = ["# ResearchMesh 本地 AIP 全文阅读", "",
             "> 本次复用已成功的 AIP 检索和核验 PDF；没有重新规划、检索或下载。",
             "> 单篇 Reader 通过官方 AIP 调用；模型结论仍须人工对照原文。", "",
             "协议完成：" + str(acceptance["protocolCompleted"]),
             "全文阅读结果通过业务校验：" + str(acceptance["businessReady"]),
             "复用已有阅读结果：" + str(acceptance["readerResultReused"]), ""]
    execution = outcome.get("execution")
    if execution:
        for item in execution["result"]["items"]:
            note = item["data"]
            lines += ["## " + note["source"]["title"], "", "方法：" + note["method"], "",
                      "发现：" + note["findings"], "", "局限：" + note["limitations"], "", "### 原文证据", ""]
            for evidence in note["evidence"]:
                lines += ["位置：" + evidence["locator"], "", "支持：" + evidence["supports"], "",
                          "出处：" + evidence["url"], "", "片段：" + (evidence.get("text") or "未提供"), ""]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="已通过的 aip-literature 输出目录")
    parser.add_argument("--pdf-proof", type=Path, required=True, help="此前核验的 pdf_resolution.json")
    parser.add_argument("--reader-root", type=Path, required=True)
    parser.add_argument("--reader-python", type=Path, default=DEFAULT_READER_PYTHON)
    parser.add_argument("--subtask", default="S2")
    parser.add_argument("--resume", type=Path, help="仅复用完成且校验通过的 reader_journal.json 所在输出目录")
    parser.add_argument("--execute", action="store_true", help="明确允许一次阅读流程，可能多次模型请求及计费")
    parser.add_argument("--record-dir", type=Path, help="指定新的记录目录；已有目录不会覆盖")
    args = parser.parse_args(argv)
    output = args.record_dir.resolve() if args.record_dir else ROOT / "outputs" / datetime.now().strftime("aip-reader-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    try:
        if importlib.metadata.version("acps-sdk") != "2.2.0":
            raise ReaderAdapterError("SDK_220_REQUIRED")
        if not args.reader_python.is_file():
            raise ReaderAdapterError("READER_PYTHON_NOT_FOUND")
        data = prepare_reader_input(args.source, args.pdf_proof, cache_dir=ROOT / "outputs/pdf-cache",
                                    subtask_id=args.subtask)
        payload = data["payload"]
        input_path = output / "reader_input.json"
        save_json(input_path, data)
        journal_source = (args.resume.resolve() / "reader_journal.json") if args.resume else None
        command = [str(args.reader_python.resolve()), "-X", "utf8", str(ROOT / "reader_aip_worker.py"),
                   "--input", str(input_path), "--reader-root", str(args.reader_root.resolve()),
                   "--record-dir", str(output)]
        if journal_source:
            command += ["--resume-journal", str(journal_source)]
        with (output / "reader_preflight.log").open("w", encoding="utf-8") as log:
            preflight = subprocess.run(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, timeout=60)
        if preflight.returncode != 0:
            failure = output / "worker_failure.json"
            code = read_json(failure).get("error", "READER_PREFLIGHT_FAILED") if failure.is_file() else "READER_PREFLIGHT_FAILED"
            raise ReaderAdapterError(code)
        package = args.reader_root.resolve() / "researchmesh_paper_reader"
        save_json(output / "environment.json", {
            "sdkVersion": "2.2.0", "wireSchemaVersion": "0.2", "readerPython": str(args.reader_python.resolve()),
            "readerRoot": str(args.reader_root.resolve()), "executeRequested": args.execute,
            "inputSources": {"aipSearch": str(args.source.resolve()), "pdfProof": str(args.pdf_proof.resolve())},
            "readerHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))}})
        if not args.execute:
            print("预检通过：真实 AIP 检索结果已绑定单篇 Reader，缓存 PDF 哈希/标题和配置已核对。")
            print("未启动 AIP Reader、未调用模型、未重新检索或下载。加 --execute 才会真实阅读。")
            print("记录：", output)
            return 0
        if journal_source:
            print("【本地 AIP 取回已完成阅读】已验证旧结果；本次不调用模型、不重复阅读。", flush=True)
        else:
            print("【本地 AIP + 单篇全文阅读】会消耗 DeepSeek 额度；可能多次模型请求，不自动重试。", flush=True)
        print("复用检索/PDF；隔离 Reader 后台执行，Leader 查询状态。记录：", output, flush=True)
        env = {"RESEARCHMESH_READER_PYTHON": str(args.reader_python.resolve()),
               "RESEARCHMESH_READER_ROOT": str(args.reader_root.resolve())}
        if journal_source:
            env["RESEARCHMESH_READER_RESUME_JOURNAL"] = str(journal_source)
        with live_partner(args.reader_root.resolve(), output, factory="aip_reader_partner:create_live_app",
                          mode="local-reader-live", extra_env=env) as (url, aic):
            outcome = asyncio.run(invoke_business(payload, url, aic, expected_task_type="paper_read",
                                                  timeout_seconds=675, poll_seconds=1))
        save_json(output / "outcome.json", outcome)
        journal_path = output / "reader_journal.json"
        journal = read_json(journal_path) if journal_path.is_file() else None
        acceptance = assess_reader_run(outcome, journal, payload)
        acceptance["partnerStopped"] = True
        save_json(output / "acceptance.json", acceptance)
        (output / "report.md").write_text(render_reader_report(outcome, acceptance), encoding="utf-8")
        print("protocolCompleted：", acceptance["protocolCompleted"])
        print("businessReady：", acceptance["businessReady"])
        if acceptance["passed"]:
            print("阅读完成：1 篇全文笔记及证据；请人工对照原文。")
        else:
            print("未完成：", ((journal or {}).get("outcome") or {}).get("error") or outcome.get("error"))
            print("不要直接重跑；成功结果可 --resume 取回，未完成记录不能自动重试模型。")
        print("临时 Partner 已停止。记录：", output)
        return 0 if acceptance["passed"] else 1
    except (Exception, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, ReaderAdapterError) else type(error).__name__
        save_json(output / "failure.json", {"error": code, "executeRequested": args.execute,
                                            "modelExecutionAttempted": None if args.execute else False,
                                            "partnerStopped": None})
        print("未完成：", code, "；请勿连续重跑。记录：", output)
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
