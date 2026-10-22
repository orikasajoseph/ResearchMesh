"""Explicit arXiv PDF resolution in the Reader runtime; never creates an LLM."""
import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
import json

from arxiv_pdf import ArxivPdfResolver
from input_binding import bind_task_input
from planner_contract import validate_plan
from result_contract import validate_result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def resolve_source(source, output, cache, search_id, reader_id):
    plan = read_json(source / "plan.json")
    validate_plan(plan)
    payload = next(t for t in plan["tasks"] if t["subtaskId"] == search_id)
    outcome, backend = read_json(source / "outcome.json"), read_json(source / "backend_outcome.json")
    from common.models import BUSINESS_TASK_FIELDS
    payload = {key: payload[key] for key in BUSINESS_TASK_FIELDS}
    if (read_json(source / "payload.json") != payload or outcome.get("error") is not None
            or backend.get("error") is not None or outcome["execution"]["state"] != "completed"
            or outcome["execution"].get("simulatedResult") is not False
            or outcome["execution"]["result"] != backend["result"]):
        raise ValueError("SEARCH_NOT_BUSINESS_READY")
    result = outcome["execution"]["result"]
    validate_result(result, payload)
    parent = next(t for t in plan["tasks"] if t["subtaskId"] == reader_id)
    bound = bind_task_input(parent, {search_id: result})
    if len(bound["input"]["papers"]) != 1:
        raise ValueError("SINGLE_READER_REQUIRED")
    # Even an API-provided PDF address needs independent source/title/hash proof.
    task = deepcopy(bound)
    task["input"]["papers"][0]["pdfUrl"] = None
    sources = deepcopy(result)
    for item in sources["items"]:
        if item["kind"] == "paper":
            item["data"]["pdfUrl"] = None
    _, events = await ArxivPdfResolver(cache).resolve(task, sources)
    save_json(output, events)
    if not events or any(e.get("state") != "verified" for e in events):
        raise ValueError("PDF_UNVERIFIED")
    return events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--search-id", required=True)
    parser.add_argument("--reader-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print("未下载；只有 --execute 允许获取已知 arXiv 来源的 PDF。")
        return 0
    if args.output.exists():
        print("记录已存在，不覆盖。")
        return 1
    try:
        asyncio.run(resolve_source(args.source.resolve(), args.output.resolve(), args.cache.resolve(),
                                   args.search_id, args.reader_id))
        print("arXiv 全文已核验；未调用模型。")
        return 0
    except Exception:
        print("全文来源未核验，停止阅读；不猜测其他论文或仓库。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
