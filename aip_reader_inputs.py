"""Bind saved successful AIP literature output to one Reader task, cache only.

No models, providers, pypdf or network here. The isolated worker rechecks the
actual PDF with pypdf before any model request.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile

from arxiv_pdf import arxiv_id, ArxivPdfResolver, MAX_PDF
from input_binding import bind_task_input, paper_ref
from reader_adapter import ReaderAdapterError, to_reader_request
from result_contract import validate_result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prepare_reader_input(source, proof_file, *, cache_dir, subtask_id="S2"):
    from run_aip_literature import select_search_task, assess_live_run
    try:
        source = Path(source).resolve()
        plan, saved_payload = read_json(source / "plan.json"), read_json(source / "payload.json")
        outcome, backend = read_json(source / "outcome.json"), read_json(source / "backend_outcome.json")
        execution = outcome["execution"]
        search_id = execution["subtaskId"]
        selected = select_search_task(plan, search_id)
        if selected != saved_payload or not assess_live_run(outcome, backend)["passed"]:
            raise ReaderAdapterError("AIP_SEARCH_CHECKPOINT_INVALID")
        search_result = execution["result"]
        validate_result(search_result, selected)
        messages = outcome["aipMessages"]
        commands = [row["command"]["command"] for row in messages]
        if (len(commands) < 2 or commands[0] != "start" or commands[-1] != "complete"
                or any(c != "get" for c in commands[1:-1])
                or messages[0]["command"]["dataItems"] != [{"type": "data", "data": selected}]
                or messages[-1]["response"]["result"]["status"]["state"] != "completed"):
            raise ReaderAdapterError("AIP_SEARCH_CHECKPOINT_INVALID")
        session_id = messages[0]["command"]["sessionId"]
        for row in messages:
            command, response = row["command"], row["response"]["result"]
            if (command["taskId"] != execution["taskId"] or response["taskId"] != execution["taskId"]
                    or command["sessionId"] != session_id or response["sessionId"] != session_id
                    or response["senderId"] != execution["agentAic"] or response["senderRole"] != "partner"):
                raise ReaderAdapterError("AIP_SEARCH_CHECKPOINT_INVALID")
        parent = next((t for t in plan["tasks"] if t["subtaskId"] == subtask_id), None)
        if (parent is None or parent["taskType"] != "paper_read" or parent["dependsOn"] != [search_id]
                or parent["constraints"].get("maxPapers") != 1):
            raise ReaderAdapterError("SINGLE_DEPENDENT_READER_REQUIRED")
        task = to_reader_request(bind_task_input(parent, {search_id: search_result}))
        paper = task["input"]["papers"][0]
        raw = deepcopy(paper)
        source_urls = [paper["url"]] if paper["url"] else []
        for item in search_result["items"]:
            if item["kind"] == "paper" and paper_ref(item["data"]) == paper:
                source_urls += item["data"].get("sourceUrls", [])
        proofs = read_json(proof_file)
        cache_dir = Path(cache_dir).resolve()
        for proof in proofs:
            original = deepcopy(paper)
            original["pdfUrl"] = None
            if (proof.get("state") != "verified" or proof.get("paperBefore") != original
                    or (paper["pdfUrl"] is not None and paper["pdfUrl"] != proof.get("pdfUrl"))):
                continue
            identity = arxiv_id(proof.get("sourceUrl"), "abs")
            if (not identity or identity not in {arxiv_id(u, "abs") for u in source_urls}
                    or arxiv_id(proof.get("pdfUrl"), "pdf") != identity):
                continue
            digest = proof.get("sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            path = (cache_dir / (digest + ".pdf")).resolve()
            if path.parent != cache_dir or not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF:
                continue
            content = path.read_bytes()
            if (sha256(content).hexdigest() != digest or len(content) != proof.get("bytes")
                    or not content.lstrip().startswith(b"%PDF-")):
                continue
            paper["pdfUrl"] = proof["pdfUrl"]
            return {"payload": to_reader_request(task), "paperBeforePdfResolution": raw,
                    "searchSource": str(source), "pdfProofSource": str(Path(proof_file).resolve()),
                    "searchTaskId": execution["taskId"], "searchSubtaskId": search_id,
                    "pdf": {"path": str(path), "sha256": digest, "bytes": len(content),
                            "sourceUrl": proof["sourceUrl"], "pdfUrl": proof["pdfUrl"],
                            "pageTitle": proof["pageTitle"], "pages": proof["pages"]},
                    "searchReused": True, "pdfDownloadedThisRun": False}
        raise ReaderAdapterError("VERIFIED_PDF_CACHE_UNAVAILABLE")
    except ReaderAdapterError:
        raise
    except (ValueError, KeyError, TypeError, OSError, StopIteration):
        raise ReaderAdapterError("AIP_READER_INPUT_INVALID") from None


async def prepare_bound_reader_input(payload, output, *, cache_dir):
    """Verify the PDF from an S1-bound AIP task before starting the existing Reader worker."""
    task = to_reader_request(payload)
    papers = task["input"]["papers"]
    if len(papers) != 1:
        raise ReaderAdapterError("SINGLE_READER_REQUIRED")
    paper = papers[0]
    identity = arxiv_id(paper["pdfUrl"], "pdf")
    if not identity or not paper["title"]:
        raise ReaderAdapterError("PDF_SOURCE_UNVERIFIED")
    source_url = "https://arxiv.org/abs/" + identity
    unresolved = deepcopy(task)
    unresolved["input"]["papers"][0]["pdfUrl"] = None
    source = {"items": [{"kind": "paper", "data": {**paper, "pdfUrl": None,
                "sourceUrls": [source_url]}}]}
    cache_dir = Path(cache_dir).resolve()
    # The proof is keyed by the exact S1 paper identity and URL. The existing
    # resolver rechecks its PDF hash, first-page title, and arXiv source before reuse.
    cache_key = sha256(json.dumps({"paper": paper, "source": source_url},
                                  sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    proof_path = cache_dir / ("bound-" + cache_key + ".json")
    previous = None
    if proof_path.is_file():
        try:
            previous = json.loads(proof_path.read_text(encoding="utf-8"))
            if not isinstance(previous, list):
                previous = None
        except (OSError, ValueError):
            previous = None
    resolver = ArxivPdfResolver(cache_dir)
    resolved, proofs = await resolver.resolve(unresolved, source, previous=previous)
    output = Path(output)
    (output / "pdf_resolution.json").write_text(json.dumps(proofs, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(proofs) != 1 or proofs[0].get("state") != "verified":
        reason = proofs[0].get("reason", "") if proofs else ""
        code = "PDF_DOWNLOAD_FAILED" if reason.startswith(("ARXIV_HTTP_", "ARXIV_NETWORK_", "PDF_CACHE_")) else "PDF_SOURCE_UNVERIFIED"
        raise ReaderAdapterError(code)
    if resolved["input"]["papers"][0]["pdfUrl"] != paper["pdfUrl"]:
        raise ReaderAdapterError("PDF_SOURCE_UNVERIFIED")
    proof = proofs[0]
    cache_path = Path(cache_dir).resolve() / (proof["sha256"] + ".pdf")
    if cache_path != Path(proof["cachePath"]).resolve() or not cache_path.is_file():
        raise ReaderAdapterError("VERIFIED_PDF_CACHE_UNAVAILABLE")
    if proof.get("networkAttempted"):
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".json.tmp", mode="w",
                                         encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(proofs, stream, ensure_ascii=False)
        try:
            temporary.replace(proof_path)
        finally:
            temporary.unlink(missing_ok=True)
    document = {"payload": task, "pdf": {"path": str(cache_path),
                "sha256": proof["sha256"], "bytes": proof["bytes"],
                "sourceUrl": proof["sourceUrl"], "pdfUrl": proof["pdfUrl"],
                "pageTitle": proof["pageTitle"], "pages": proof["pages"]},
                "searchSource": "current-aip-bound-task", "searchReused": False,
                "pdfDownloadedThisRun": bool(proof["networkAttempted"])}
    with (output / "reader_input.json").open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
    return document
