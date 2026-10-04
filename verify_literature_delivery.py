"""离线检查显式指定的张交付包：历史样例回放及真实 Wrapper 加上游替身。

不访问 Semantic Scholar，不读取 Key，不声称验证了在线检索或 AIP 网络。
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from literature_adapter import call_wrapper, load_wrapper, normalize_response, to_wrapper_request, verify_replay_request
from run_literature import ROOT, select_task


async def verify(root):
    wrapper = load_wrapper(root)
    records = []
    for case in ("success", "empty"):
        request_path = root / "examples" / f"literature_{case}_input.json"
        output_path = root / "examples" / f"literature_{case}_output.json"
        fixture_request = json.loads(request_path.read_text(encoding="utf-8"))
        fixture_result = json.loads(output_path.read_text(encoding="utf-8"))
        wrapper.LiteratureSearchRequest.model_validate(fixture_request)
        wrapper.PaperRecordsResponse.model_validate(fixture_result)
        plan = json.loads((ROOT / "examples/literature_alphaevolve_v02.json").read_text(encoding="utf-8"))
        plan["userQuery"] = "回放队友交付样例：" + case
        plan["tasks"][0]["input"]["query"] = fixture_request["query"]
        plan["tasks"][0]["constraints"]["maxResults"] = fixture_request["limit"]
        payload = select_task(plan)
        request = to_wrapper_request(payload, fixture_request["requestId"])
        verify_replay_request(request, fixture_request)
        outcome = normalize_response(payload, request, fixture_result)
        assert outcome["error"] is None
        records.append({"case": "delivered_" + case, "passed": True,
                        "returned": len(outcome["result"]["items"]),
                        "fixtureSha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                        "outcome": outcome["diagnostics"].get("outcome")})

    payload = select_task(json.loads((ROOT / "examples/literature_alphaevolve_v02.json").read_text(encoding="utf-8")))
    calls = []
    class OfflineClient:
        async def search_paper(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(total=1, items=[SimpleNamespace(raw_data={
                "paperId": "offline-fixture", "title": "Offline synthetic test paper",
                "authors": [{"name": "Test author"}], "year": 2025,
                "url": "https://example.invalid/paper", "externalIds": {"ArXiv": "2506.13131"},
                "openAccessPdf": None})])
    provider = wrapper.SemanticScholarProvider(
        config=wrapper.LiteratureAgentConfig(api_key=None, max_retries=0), client=OfflineClient())
    # This exercises Zhang's actual agent, provider normalization and request model, but no real SDK/network call.
    outcome = await call_wrapper(payload, "offline-wrapper", wrapper.LiteratureAgent(provider), wrapper.LiteratureSearchRequest)
    assert outcome["result"]["items"][0]["data"]["repositoryUrls"] == []
    assert calls[0]["query"] == "AlphaEvolve" and calls[0]["limit"] == 5
    records.append({"case": "actual_wrapper_with_sdk_stub", "passed": True, "networkUsed": False})
    repeated = await call_wrapper(payload, "offline-wrapper", wrapper.LiteratureAgent(provider), wrapper.LiteratureSearchRequest)
    assert repeated["diagnostics"]["metrics"]["cacheHit"] is True and len(calls) == 1
    records.append({"case": "same_request_cache", "passed": True})
    # Document, don't silently patch, the upstream cache behavior across request IDs.
    await call_wrapper(payload, "offline-wrapper-new-id", wrapper.LiteratureAgent(provider), wrapper.LiteratureSearchRequest)
    records.append({"case": "different_request_id_cache_observation", "cacheSharedAcrossIds": len(calls) == 1,
                    "note": "交付包 cache_key 包含 requestId；不同任务 ID 的同查询不能复用缓存。"})
    output = ROOT / "outputs" / datetime.now().strftime("zhang-delivery-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((root / "researchmesh_literature").glob("*.py"))}
    evidence = {"verifiedAt": datetime.now().astimezone().isoformat(), "mode": "offline-delivery-check",
                "networkUsed": False, "aipUsed": False, "liveResearchVerified": False,
                "records": records, "sourceHashes": hashes}
    (output / "acceptance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("张交付包离线检查通过；未访问真实 API 或 AIP。")
    print("记录：", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper-root", required=True)
    args = parser.parse_args()
    asyncio.run(verify(Path(args.wrapper_root).resolve()))
