"""Local-only runtime/identity checks; no Provider, model, PDF, clone or MQ."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.platform_preflight import Reporter, check_identity, AICS
from aip.literature_audit import AUDIT_ROOT


def runtime_probe(python, agent, env_file, runtime_home):
    # Secrets travel only through existing environment or env_file, never command text/output.
    program = '''
import json,socket,sys,os,traceback
from pathlib import Path
from types import SimpleNamespace
def denied(*args,**kwargs): raise RuntimeError("OFFLINE_NETWORK_FORBIDDEN")
socket.socket.connect=denied
socket.socket.connect_ex=denied
socket.getaddrinfo=denied
try:
    if sys.argv[1]=="reader":
        from reader_aip_worker import check_runtime
        from verify_reader_aip import fixture_task
        check_runtime(SimpleNamespace(reader_root=Path.cwd()/"agents/paper_reader"),fixture_task(),Path(sys.argv[2]),Path(sys.argv[3]))
    else:
        os.environ["TIKTOKEN_CACHE_DIR"]=str(Path(sys.prefix)/"runtime-cache/tiktoken")
        os.environ["PYTHON_DOTENV_DISABLED"]="1"
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"]="True"
        from repository_aip_worker import runtime_check
        runtime_check(Path.cwd()/"agents/repository")
    print(json.dumps({"passed":True,"code":"LOCAL_RUNTIME_VALID"}))
except Exception as error:
    code=getattr(error,"code",None)
    if code not in {"MISSING_READER_CONFIGURATION","READER_RUNTIME_DEPENDENCIES_MISSING","READER_EMBEDDING_CACHE_MISSING"}:
        code="LOCAL_RUNTIME_INVALID"
    frames=[{"file":Path(frame.filename).name,"function":frame.name,"line":frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)]
    print(json.dumps({"passed":False,"code":code,"exceptionCategory":type(error).__name__,"frames":frames}))
'''
    try:
        process = subprocess.run([str(python), "-c", program, agent, str(env_file), str(runtime_home)],
            cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                              TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1"),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        result = json.loads(process.stdout.splitlines()[-1])
        return result if process.returncode == 0 else dict(passed=False, code="RUNTIME_PROCESS_FAILED")
    except subprocess.TimeoutExpired:
        return dict(passed=False, code="LOCAL_RUNTIME_PROBE_TIMEOUT")
    except (OSError, ValueError, IndexError):
        return dict(passed=False, code="RUNTIME_PROBE_UNAVAILABLE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--reader-python", type=Path, required=True)
    parser.add_argument("--repository-python", type=Path, required=True)
    parser.add_argument("--reader-runtime-home", type=Path, required=True)
    args = parser.parse_args()
    identities = Reporter()
    for agent in ("reader", "repository"):
        check_identity(identities, agent, AICS[agent])
    runtimes = {agent: runtime_probe(python, agent, args.env_file, args.reader_runtime_home)
                for agent, python in (("reader", args.reader_python), ("repository", args.repository_python))}
    report = dict(scope="OFFLINE_ANALYSIS_GROUP_READINESS_NOT_FORMAL_ACCEPTANCE",
        atUtc=datetime.now(timezone.utc).isoformat(),
        checks=[asdict(check) for check in identities.checks], runtimes=runtimes,
        localChecksPassed=all(c.state == "PASS" for c in identities.checks) and all(r["passed"] for r in runtimes.values()),
        providerCreated=False, modelCalled=False, pdfDownloaded=False, repositoryCloned=False,
        mqConnected=False, taskSent=False, formalAcceptancePassed=False)
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    output = AUDIT_ROOT / ("analysis-group-readiness-" + uuid4().hex + ".json")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(dict(report, reportPath=str(output)), ensure_ascii=False, indent=2))
    return 0 if report["localChecksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
