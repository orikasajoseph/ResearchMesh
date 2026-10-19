"""Health-only local Reader AIP server check. Never sends a task/start or runs Reader."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_aip_literature import live_partner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="已通过免费的 Reader 预检目录")
    parser.add_argument("--reader-root", type=Path, required=True)
    parser.add_argument("--reader-python", type=Path, required=True)
    parser.add_argument("--reader-env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--reader-runtime-home", type=Path)
    args = parser.parse_args()
    source = args.input_dir.resolve()
    if source.parent != ROOT / "outputs":
        raise ValueError("OWNED_PREFLIGHT_DIRECTORY_REQUIRED")
    preflight = json.loads((source / "reader_preflight.json").read_text(encoding="utf-8"))
    if preflight.get("passed") is not True or preflight.get("modelExecutionAttempted") is not False:
        raise ValueError("FREE_PREFLIGHT_REQUIRED")
    output = ROOT / "outputs" / datetime.now().strftime("reader-aip-startup-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    (output / "reader_input.json").write_bytes((source / "reader_input.json").read_bytes())
    with live_partner(args.reader_root.resolve(), output, factory="aip_reader_partner:create_live_app",
            mode="local-reader-live", extra_env={"RESEARCHMESH_READER_PYTHON": str(args.reader_python.resolve()),
                                               "RESEARCHMESH_READER_ROOT": str(args.reader_root.resolve()),
                                               "RESEARCHMESH_READER_ENV_FILE": str(args.reader_env_file.resolve()),
                                               **({"RESEARCHMESH_READER_RUNTIME_HOME": str(args.reader_runtime_home.resolve())}
                                                  if args.reader_runtime_home else {})}):
        # live_partner only queries /health; deliberately never send an AIP start.
        pass
    no_worker = not (output / "reader_journal.json").exists() and not (output / "reader_worker.log").exists()
    record = {"passed": no_worker, "scope": "LOCAL_READER_AIP_HEALTH_ONLY_NO_TASK_NO_LLM_NO_PDF",
              "serverStarted": True, "partnerStopped": True, "readerWorkerStarted": False if no_worker else None,
              "taskSent": False, "modelCalled": False if no_worker else None}
    (output / "acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(record, record=str(output / "acceptance.json")), ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
