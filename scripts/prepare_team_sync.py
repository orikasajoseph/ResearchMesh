"""Read-only Git candidate audit. No add, commit, push or secret values in output."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_-]{20,}", r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]


def classify(path, text, secrets):
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    forbidden = (any(p in {"outputs", ".venv", "venv", "runtime-cache", "node_modules"} for p in parts)
        or (Path(path).name.startswith(".env") and Path(path).name != ".env.example")
        or Path(path).suffix.lower() in {".key", ".pem", ".p12", ".pfx"})
    if forbidden:
        return "DO_NOT_UPLOAD"
    if Path(path).suffix.lower() == ".pdf":
        return "REVIEW_PDF_LICENSE_AND_PROVENANCE"
    if text is not None and (any(s in text for s in secrets) or any(p.search(text) for p in PATTERNS)):
        return "POSSIBLE_SECRET_DO_NOT_UPLOAD"
    return "CANDIDATE_REQUIRES_DIFF_REVIEW"


def audit(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args]).decode("utf-8", errors="strict")
    values = dict(dotenv_values(root / ".env", interpolate=False))
    values.update(os.environ)
    secrets = [v for k, v in values.items() if re.search(r"KEY|TOKEN|PASSWORD|SECRET", k, re.I)
               and isinstance(v, str) and len(v) >= 12]
    names = sorted(set(n for n in git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0") if n))
    candidates = []
    for name in names:
        path = root / name
        # Deleted tracked paths need diff review but no read; never follow links outside root.
        if not path.resolve().is_relative_to(root.resolve()):
            category = "DO_NOT_UPLOAD"
        else:
            text = None
            if path.is_file() and path.suffix.lower() in {".py", ".md", ".json", ".txt", ".toml", ".yml", ".yaml", ".example"}:
                text = path.read_text(encoding="utf-8", errors="replace")
            category = classify(name, text, secrets)
        candidates.append({"file": name, "assessment": category})
    return {"scope": "READ_ONLY_CURRENT_GIT_CANDIDATES_NOT_HISTORY_SCAN",
            "branch": git("branch", "--show-current").strip(), "commit": git("rev-parse", "HEAD").strip(),
            "candidateCount": len(candidates), "files": candidates,
            "requiresHumanDiffReview": True, "gitMutations": 0,
            "notes": "No secret values reported. Ignored files are excluded; history and remote are not scanned."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit(ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    warnings = [r for r in result["files"] if r["assessment"] != "CANDIDATE_REQUIRES_DIFF_REVIEW"]
    print("只读检查完成；候选文件", result["candidateCount"], "；需特别检查", len(warnings))
    for row in warnings:
        print(row["assessment"], row["file"])
    print("未提交或推送；检查结果：", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
