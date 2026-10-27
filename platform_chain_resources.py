"""Prepare independently evidenced Reader inputs from a saved S1 result.

No model calls. Network access occurs only in prepare_reader_resources; the
validator is offline and preserves the original six-field S1 Result.
"""
from copy import deepcopy
from hashlib import sha256
import asyncio
import json
import re
from pathlib import Path

import httpx

from arxiv_pdf import (
    ArxivPdfResolver, arxiv_id, validate_pdf_enrichment, normalize_title, MAX_PDF, inspect_pdf,
)
from input_binding import github_repository_url, paper_ref
from repository_resource import resolve_resource, SCOPE as REPOSITORY_SCOPE


SCOPE = "FORMAL_CHAIN_READER_RESOURCE_BINDING_V1"


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def selected_search_data(task, search_result):
    papers = task["input"].get("papers", [])
    if len(papers) != 1:
        raise ValueError("RESOURCE_SINGLE_PAPER_REQUIRED")
    matches = [item["data"] for item in search_result["items"]
               if item["kind"] == "paper" and paper_ref(item["data"]) == papers[0]]
    if len(matches) != 1:
        raise ValueError("RESOURCE_SOURCE_PAPER_MISMATCH")
    return deepcopy(matches[0])


def validate_preparation(bundle, original, search_result):
    """Allow only independently evidenced PDF/repository additions to one ref."""
    if (bundle.get("scope") != SCOPE or bundle.get("originalTaskSha256") != digest(original)
            or bundle.get("searchResultSha256") != digest(search_result)
            or bundle.get("modelCalls") != 0):
        raise ValueError("RESOURCE_BINDING_SOURCE_CHANGED")
    search_data = selected_search_data(original, search_result)
    prepared = deepcopy(bundle["task"])
    before = original["input"]["papers"][0]
    after = prepared["input"]["papers"][0]
    if before["pdfUrl"] != after["pdfUrl"]:
        proofs = bundle.get("pdfProofs", [])
        proof = proofs[0] if len(proofs) == 1 else {}
        sources = [before.get("url"), *search_data.get("sourceUrls", [])]
        identity = arxiv_id(proof.get("sourceUrl"), "abs")
        if (before["pdfUrl"] is not None or proof.get("state") != "verified"
                or proof.get("paperBefore") != before or not identity
                or identity not in {arxiv_id(url, "abs") for url in sources}
                or proof.get("pdfUrl") != after["pdfUrl"]
                or arxiv_id(after["pdfUrl"], "pdf") != identity
                or not isinstance(proof.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", proof["sha256"])
                or normalize_title(proof.get("pageTitle")) != normalize_title(before["title"])
                or type(proof.get("bytes")) is not int or not 0 < proof["bytes"] <= MAX_PDF
                or type(proof.get("pages")) is not int or proof["pages"] < 1):
            raise ValueError("RESOURCE_PDF_PROOF_INVALID")
    if not after["pdfUrl"]:
        raise ValueError("VERIFIED_PDF_URL_UNAVAILABLE")
    if before["repositoryUrls"] != after["repositoryUrls"]:
        resource = bundle.get("repositoryProof") or {}
        request_paper = deepcopy(before)
        request_paper["pdfUrl"] = after["pdfUrl"]
        request = {"paper": request_paper, "searchData": search_data,
                   "explicitRepositoryUrls": []}
        proof = resource.get("proof") or {}
        urls = resource.get("repositoryUrls", [])
        if (before["repositoryUrls"] or resource.get("scope") != REPOSITORY_SCOPE
                or resource.get("request") != request or resource.get("modelCalls") != 0
                or resource.get("state") != "selected"
                or resource.get("selectionMode") != "verified_public_readme"
                or resource.get("verifiedRelation") is not True
                or len(urls) != 1 or not github_repository_url(urls[0])
                or urls != after["repositoryUrls"] or proof.get("repositoryUrl") != urls[0]
                or proof.get("titleMatched") is not True or proof.get("identifierMatched") is not True
                or proof.get("evidenceKind") != "public_github_readme_title_and_paper_identifier"):
            raise ValueError("RESOURCE_REPOSITORY_PROOF_INVALID")
    if len(after["repositoryUrls"]) != 1 or not github_repository_url(after["repositoryUrls"][0]):
        raise ValueError("VERIFIED_REPOSITORY_URL_UNAVAILABLE")
    # Restore the only two fields allowed to change; all other input, identity
    # and task fields must still match the exact upstream-bound task.
    after["pdfUrl"] = before["pdfUrl"]
    after["repositoryUrls"] = deepcopy(before["repositoryUrls"])
    if prepared != original:
        raise ValueError("RESOURCE_BINDING_TASK_CHANGED")
    return deepcopy(bundle["task"])


def verify_cached_preparation(bundle, original, search_result, *, cache_dir):
    task = validate_preparation(bundle, original, search_result)
    if original["input"]["papers"][0]["pdfUrl"] != task["input"]["papers"][0]["pdfUrl"]:
        proof = bundle["pdfProofs"][0]
        root = Path(cache_dir).resolve()
        path = (root / (proof["sha256"] + ".pdf")).resolve()
        if (path.parent != root or path != Path(proof["cachePath"]).resolve()
                or not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF):
            raise ValueError("VERIFIED_PDF_CACHE_UNAVAILABLE")
        content = path.read_bytes()
        if (sha256(content).hexdigest() != proof["sha256"] or len(content) != proof["bytes"]
                or inspect_pdf(content, task["input"]["papers"][0]["title"]) != proof["pages"]):
            raise ValueError("VERIFIED_PDF_CACHE_CHANGED")
    return task


async def prepare_reader_resources(task, search_result, *, cache_dir,
                                   pdf_resolver=None, repository_client=None, checkpoint=None):
    data = selected_search_data(task, search_result)
    resolver = pdf_resolver or ArxivPdfResolver(cache_dir)
    resolved, pdf_proofs = await resolver.resolve(task, search_result)
    validate_pdf_enrichment(task, resolved)
    paper = resolved["input"]["papers"][0]
    resource = None
    if paper["pdfUrl"] and not paper["repositoryUrls"]:
        request = {"paper": deepcopy(paper), "searchData": data,
                   "explicitRepositoryUrls": []}
        if repository_client is None:
            def lookup():
                with httpx.Client(trust_env=False) as client:
                    return resolve_resource(request, client)
            resource = await asyncio.to_thread(lookup)
        else:
            resource = await asyncio.to_thread(resolve_resource, request, repository_client)
        paper["repositoryUrls"] = deepcopy(resource.get("repositoryUrls", []))
    bundle = {"scope": SCOPE, "originalTaskSha256": digest(task),
              "searchResultSha256": digest(search_result), "task": resolved,
              "pdfProofs": pdf_proofs, "repositoryProof": resource, "modelCalls": 0}
    if checkpoint is not None:
        checkpoint(deepcopy(bundle))
    validate_preparation(bundle, task, search_result)
    return bundle
