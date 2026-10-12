"""Isolated Reader process used INSIDE a local AIP Partner, no SDK required.

Default is offline preflight. --execute is a paid opt-in. Success is journaled
before returning; only a completed exact checkpoint may be reused without LLM.
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import sys

from arxiv_pdf import inspect_pdf, MAX_PDF
from reader_adapter import ReaderAdapterError, call_reader, load_reader, normalize_reader_result, to_reader_request
from reader_runtime import prepare_live_reader
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent


def inspect_input(data):
    task = to_reader_request(data["payload"])
    if task != data["payload"]:
        raise ReaderAdapterError("INVALID_READER_TASK")
    pdf = data["pdf"]
    digest = pdf["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ReaderAdapterError("READER_PDF_CACHE_INVALID")
    path = (ROOT / "outputs/pdf-cache" / (digest + ".pdf")).resolve()
    if path != Path(pdf["path"]).resolve() or not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF:
        raise ReaderAdapterError("READER_PDF_CACHE_INVALID")
    content = path.read_bytes()
    paper = task["input"]["papers"][0]
    if (hashlib.sha256(content).hexdigest() != digest or len(content) != pdf["bytes"]
            or paper["pdfUrl"] != pdf["pdfUrl"]):
        raise ReaderAdapterError("READER_PDF_CACHE_INVALID")
    pages = inspect_pdf(content, paper["title"])
    if pages != pdf["pages"]:
        raise ReaderAdapterError("READER_PDF_CACHE_INVALID")
    return task, content


def reuse_completed(journal, task, pdf):
    if journal.get("phase") != "completed":
        raise ReaderAdapterError("READER_PREVIOUS_EXECUTION_UNCERTAIN")
    if (journal.get("scope") != "LIVE_READER_LOCAL_PDF_WORKER" or journal.get("task") != task
            or journal.get("pdf", {}).get("sha256") != pdf["sha256"]
            or journal.get("modelExecutionAttemptedEver") is not True
            or not isinstance(journal.get("outcome"), dict)
            or journal["outcome"].get("error") is not None):
        raise ReaderAdapterError("INVALID_READER_CHECKPOINT")
    normalized = normalize_reader_result(task, journal["outcome"]["result"])
    if normalized["error"] or normalized["diagnostics"].get("analysisLevel") != "fulltext":
        raise ReaderAdapterError("INVALID_READER_CHECKPOINT")
    return normalized


async def execute_one(task, content, prepared, *, timeout_seconds=600, save_started=None):
    if save_started:
        save_started()
    # Existing single-paper adapter: no Leader/worker retry.
    return await call_reader(task, *prepared, timeout_seconds=timeout_seconds)


async def run(args):
    output = Path(args.record_dir).resolve()
    if not output.is_dir() or output.parent != ROOT / "outputs":
        raise ReaderAdapterError("INVALID_READER_RECORD_DIRECTORY")
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    task, content = inspect_input(data)
    wrapper = load_reader(args.reader_root)
    wrapper.PaperReadTask.model_validate(task)
    resume = json.loads(Path(args.resume_journal).read_text(encoding="utf-8")) if args.resume_journal else None
    reused = reuse_completed(resume, task, data["pdf"]) if resume else None
    if not reused:
        values = dict(dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False))
        for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
            values[key] = os.environ.get(key, values.get(key))
            if not isinstance(values[key], str) or not values[key].strip():
                raise ReaderAdapterError("MISSING_READER_CONFIGURATION")
        if any(importlib.util.find_spec(name) is None for name in ("paperqa", "torch", "sentence_transformers")):
            raise ReaderAdapterError("READER_RUNTIME_DEPENDENCIES_MISSING")
        if not (Path(sys.prefix) / "runtime-cache/huggingface").is_dir():
            raise ReaderAdapterError("READER_EMBEDDING_CACHE_MISSING")
        config = wrapper.PaperReaderConfig(api_key=values["DEEPSEEK_API_KEY"],
                                          api_base=values["DEEPSEEK_BASE_URL"], model=values["DEEPSEEK_MODEL"])
        config.provider_model  # local format validation only; no Provider or requests
    if not args.execute:
        record = {"passed": True, "scope": "READER_PREFLIGHT_ONLY_NO_MODEL_NO_NETWORK",
                  "task": task, "pdf": data["pdf"], "readerSchemaValid": True,
                  "pdfHashAndTitleVerified": True, "resumeResultValidated": bool(reused),
                  "providerCreated": False, "modelExecutionAttempted": False}
        (output / "reader_preflight.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Reader预检通过；已核对PDF哈希/标题、单篇任务和真实Wrapper，未调用模型。")
        return 0
    journal = {"scope": "LIVE_READER_LOCAL_PDF_WORKER", "phase": "preparing", "task": task,
               "pdf": data["pdf"], "modelExecutionAttemptedThisRun": False,
               "modelExecutionAttemptedEver": False, "modelRequestCount": None,
               "readerResultReused": False, "automaticRetries": 0, "outcome": None}
    path = output / "reader_journal.json"
    def save():
        journal["updatedAt"] = datetime.now().astimezone().isoformat()
        temporary = output / "reader_journal.json.tmp"
        temporary.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    save()
    previous_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        if reused:
            journal.update(phase="completed", outcome=reused, readerResultReused=True,
                           modelExecutionAttemptedEver=True, resumeFrom=str(Path(args.resume_journal).resolve()))
        else:
            async def fetch(url):
                if url != task["input"]["papers"][0]["pdfUrl"]:
                    raise ReaderAdapterError("LOCAL_PDF_SOURCE_MISMATCH")
                return content
            prepared = prepare_live_reader(args.reader_root, env_file=ROOT / ".env", pdf_fetcher=fetch)
            def started():
                journal.update(phase="running", modelExecutionAttemptedThisRun=True,
                               modelExecutionAttemptedEver=True)
                save()
            outcome = await execute_one(task, content, prepared, save_started=started)
            if outcome["error"] is None and outcome["diagnostics"].get("analysisLevel") != "fulltext":
                outcome = {"result": None, "error": {"code": "INVALID_READER_RESULT"}, "diagnostics": {}}
            journal.update(phase="completed" if outcome["error"] is None else "failed", outcome=outcome)
    except asyncio.CancelledError:
        journal["phase"] = "interrupted"
        raise
    except Exception as error:
        code = error.code if isinstance(error, ReaderAdapterError) else "READER_WORKER_FAILED"
        journal.update(phase="failed", outcome={"result": None, "error": {"code": code}, "diagnostics": {}})
    finally:
        logging.disable(previous_logging)
        save()
    return 0 if journal["phase"] == "completed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--reader-root", required=True)
    parser.add_argument("--record-dir", required=True)
    parser.add_argument("--resume-journal")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (Exception, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, ReaderAdapterError) else type(error).__name__
        output = Path(args.record_dir).resolve()
        if output.is_dir() and output.parent == ROOT / "outputs":
            (output / "worker_failure.json").write_text(json.dumps({"error": code}), encoding="utf-8")
        print("Reader worker：", code, "；不要自动重试。")
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
