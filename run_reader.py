"""指定本地 Transformer PDF 的单篇阅读测试；默认仅预检，--execute 才使用模型。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
from io import BytesIO
import json
import logging
import math
from pathlib import Path
import re
import time

from reader_adapter import ReaderAdapterError, call_reader, load_reader, to_reader_request
from reader_runtime import prepare_live_reader

ROOT = Path(__file__).resolve().parent
PDF_URL = "https://arxiv.org/pdf/1706.03762"
MAX_PDF_BYTES = 50 * 1024 * 1024


def inspect_pdf(path):
    """仅检查本例指定论文，不把任意本地 PDF 冒充成该论文。"""
    from pypdf import PdfReader

    path = Path(path).resolve()
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF_BYTES:
            raise ReaderAdapterError("LOCAL_PDF_MISSING_OR_TOO_LARGE")
        content = path.read_bytes()
        if len(content) > MAX_PDF_BYTES or not content.lstrip().startswith(b"%PDF-"):
            raise ReaderAdapterError("INVALID_LOCAL_PDF")
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted or not reader.pages:
            raise ReaderAdapterError("INVALID_LOCAL_PDF")
        texts = [page.extract_text() or "" for page in reader.pages]
        first = re.sub(r"\s+", " ", texts[0])
        if "Attention Is All You Need" not in first or "1706.03762" not in first:
            raise ReaderAdapterError("PDF_DOES_NOT_MATCH_TEST_PAPER")
        if sum(map(len, texts)) < 1000:
            raise ReaderAdapterError("LOCAL_PDF_TEXT_UNAVAILABLE")
    except ReaderAdapterError:
        raise
    except Exception:
        raise ReaderAdapterError("LOCAL_PDF_INSPECTION_FAILED") from None
    return content, {"path": str(path), "bytes": len(content),
                     "sha256": hashlib.sha256(content).hexdigest(),
                     "pages": len(texts), "textCharacters": sum(map(len, texts)),
                     "sourceUrl": PDF_URL, "sourceUrlIndependentlyDownloaded": False}


def make_task():
    return to_reader_request({
        "schemaVersion": "0.2", "subtaskId": "R1", "taskType": "paper_read",
        "goal": "阅读指定论文，概括研究问题、方法、发现与作者陈述的局限，保留原文证据。",
        "input": {"papers": [{"paperId": "arxiv:1706.03762", "title": "Attention Is All You Need",
            "doi": None, "url": "https://arxiv.org/abs/1706.03762", "pdfUrl": PDF_URL,
            "repositoryUrls": []}]},
        "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1"})


def local_pdf_fetcher(content):
    async def fetch(url):
        if url != PDF_URL:
            raise ReaderAdapterError("LOCAL_PDF_SOURCE_MISMATCH")
        return content
    return fetch


def render_report(record):
    lines = ["# ResearchMesh 单篇真实阅读测试", "",
             "> 本地 PDF 直接调用 Paper Reader；未使用 Planner、论文检索、ADP 或 AIP。",
             "> 模型笔记及证据关联仍需人工对照原文，不自动视为已核验科研结论。", "",
             "运行状态：" + record["metadata"]["outcome"], ""]
    outcome = record.get("readerOutcome")
    result = outcome.get("result") if outcome else None
    if result:
        lines += [result["summary"], ""]
        for item in result["items"]:
            note = item["data"]
            lines += ["## " + note["source"]["title"], "",
                      "原始 PDF 地址：" + note["source"]["pdfUrl"], "",
                      "方法：" + note["method"], "", "发现：" + note["findings"], "",
                      "局限：" + note["limitations"], "", "### 证据", ""]
            for evidence in note["evidence"]:
                lines += ["位置：" + evidence["locator"], "",
                          "支持的内容：" + evidence["supports"], "",
                          "原文：" + (evidence.get("text") or "未提供"), ""]
        lines += ["警告：" + ("；".join(result["warnings"]) or "无"), ""]
    if record.get("error"):
        lines += ["未完成：" + record["error"]["code"], ""]
    return "\n".join(lines)


async def run(args):
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ReaderAdapterError("INVALID_TIMEOUT")
    content, pdf = inspect_pdf(args.pdf)
    task = make_task()
    wrapper = load_reader(args.wrapper_root)
    wrapper.PaperReadTask.model_validate(task)
    if not args.execute:
        print(f"预检通过：{pdf['pages']} 页；单篇 v0.2 任务与真实 Wrapper 兼容。")
        print("未创建 Provider、未调用模型、未下载 PDF。加 --execute 才会真实阅读并消耗额度。")
        return 0

    out = ROOT / "outputs" / ("reader-live-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True, exist_ok=False)
    package = Path(args.wrapper_root).resolve() / "researchmesh_paper_reader"
    record = {"scope": "REAL_LOCAL_PDF_READER_DIRECT_NO_PLANNER_NO_SEARCH_NO_ADP_NO_AIP",
              "task": task, "localPdf": pdf, "wrapperRoot": str(package.parent),
              "wrapperSourceHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(package.glob("*.py"))},
              "metadata": {"outcome": "preparing", "modelExecutionAttempted": False,
                  "modelRequestCount": None, "plannerUsed": False, "literatureSearchUsed": False,
                  "adpUsed": False, "aipUsed": False, "syntheticData": False,
                  "pdfFetchMode": "local-file", "leaderAutomaticRetries": 0,
                  "timeoutSeconds": args.timeout},
              "readerOutcome": None, "error": None}
    def save():
        (out / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "report.md").write_text(render_report(record), encoding="utf-8")
    save()
    print("【真实单篇阅读】会消耗 DeepSeek 额度；PaperQA 可能发出多次模型请求。")
    print("只进行一次阅读流程，不在 Leader 层自动重试；超时也可能已产生费用。")
    print("记录目录：", out)
    started = time.perf_counter()
    previous_logging_level = logging.root.manager.disable
    try:
        # 减少第三方库日志意外回显配置；错误记录仅使用静态错误码。
        logging.disable(logging.CRITICAL)
        prepared = prepare_live_reader(args.wrapper_root, env_file=ROOT / ".env",
            runtime_home=args.runtime_home, pdf_fetcher=local_pdf_fetcher(content))
        record["metadata"].update(outcome="reading", modelExecutionAttempted=True)
        save()
        outcome = await call_reader(task, *prepared, timeout_seconds=args.timeout)
        record["readerOutcome"] = outcome
        record["error"] = outcome["error"]
        record["metadata"]["outcome"] = "completed" if outcome["error"] is None else "failed"
        analysis = getattr(prepared[0], "last_provider_analysis", None)
        if analysis is not None:
            record["providerTimings"] = analysis.timings
    except asyncio.CancelledError:
        record["metadata"]["outcome"] = "interrupted"
        record["error"] = {"code": "READER_INTERRUPTED"}
        raise
    except ReaderAdapterError as exc:
        record["error"] = {"code": exc.code}
        record["metadata"]["outcome"] = "failed"
    except Exception:
        record["error"] = {"code": "READER_ENTRY_FAILED"}
        record["metadata"]["outcome"] = "failed"
    finally:
        logging.disable(previous_logging_level)
        record["metadata"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
        save()
    if record["error"]:
        print("阅读未完成：", record["error"]["code"], "；不要连续重跑，先查看记录。")
        return 1
    print("阅读完成：返回 1 篇全文笔记及证据；请打开 report.md 对照原文检查。")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper-root", required=True)
    parser.add_argument("--pdf", default=str(ROOT / "examples" / "reader_test.pdf"))
    parser.add_argument("--runtime-home")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--execute", action="store_true", help="显式许可调用模型，可能计费")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except ReaderAdapterError as exc:
        print("未执行阅读：", exc.code)
        return 1
    except KeyboardInterrupt:
        print("已中断；若已进入模型流程，可能产生费用，记录目录保留。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
