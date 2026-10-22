"""Export a fixed numeric allowlist from verified records; no raw logs or PDFs."""
import argparse
import json
from pathlib import Path

from aip_reader_inputs import read_json
from assemble_aip_evidence import assemble

ROOT = Path(__file__).resolve().parent


def sanitized_evidence(record, regression):
    if record["acceptance"].get("passed") is not True:
        raise ValueError("UNVERIFIED_AIP_EVIDENCE")
    count = regression.get("testsPassed")
    if (not isinstance(count, int) or isinstance(count, bool) or count < 1
            or regression.get("httpChecksPassed") is not True
            or not regression.get("scenarios")
            or not all(s.get("passed") is True for s in regression.get("scenarios", []))):
        raise ValueError("UNVERIFIED_REGRESSION")
    rows = []
    allowed = {"literature.search", "paper.analyze"}
    for row in record["executionRecords"]:
        if row["requiredSkill"] not in allowed or row["status"] != "completed":
            raise ValueError("UNEXPECTED_TASK")
        latency = row["latencyMs"]
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not 0 <= latency < float("inf"):
            raise ValueError("INVALID_LATENCY")
        rows.append({"skill": row["requiredSkill"], "state": "completed", "latencyMs": latency,
                     "itemCount": len(row["result"]["items"]), "evidenceCount": len(row["evidence"])})
    return {"scope": "SANITIZED_SUMMARY_OF_TWO_PREVIOUS_LOCAL_AIP_RUNS",
        "businessSchemaVersion": "0.2", "sdkVersion": "2.2.0", "tasks": rows,
        "platformDiscoveryVerified": False, "tlsIdentityVerified": False,
        "singleFreshEndToEndRun": False, "repositoryExecuted": False,
        "scientificClaimsHumanVerified": False, "newApiCallsByExport": 0,
        "regression": {"testsPassed": count, "testsSkipped": regression["testsSkipped"],
            "mockScenarioCount": len(regression["scenarios"]), "localHttpChecksPassed": True},
        "sourceHashes": [{"file": Path(m["path"]).name, "sha256": m["sha256"]}
                         for m in record["sourceManifest"]],
        "omitted": ["apiKeys", "certificates", "usernames", "absolutePaths", "researchQuery",
                    "rawMessages", "paperText", "modelClaims", "pdfFiles", "agentIdentities"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", type=Path, required=True)
    parser.add_argument("--reader", type=Path, required=True)
    parser.add_argument("--regression", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    record = assemble(args.search, args.reader, cache_dir=ROOT / "outputs/pdf-cache")
    result = sanitized_evidence(record, read_json(args.regression))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print("脱敏摘要已保存（不覆盖旧文件）；未联网：", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
