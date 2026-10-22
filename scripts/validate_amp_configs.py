"""Offline structural validation for the three AMP heartbeat collectors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

AGENTS = ("literature", "reader", "repository")
REQUIRED = ("Name", "Path", "Tag", "Parser", "DB", "Brokers", "Topics", "Format")


def validate_directory(root: Path) -> dict:
    records = []
    db_paths = set()
    for agent in AGENTS:
        name = ("fluent-bit-heartbeat-platform.conf" if agent == "literature"
                else f"fluent-bit-heartbeat-{agent}-platform.conf")
        path = root / name
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        checks = {
            "required_keys": all(any(line.strip().startswith(key + " ") for line in text.splitlines())
                                 for key in REQUIRED),
            "heartbeat_path_matches_agent": f"amp_heartbeat_{agent}_live.jsonl" in text,
            "database_matches_agent": ("fluentbit-heartbeat-platform.db" in text if agent == "literature"
                                       else f"fluentbit-heartbeat-{agent}-platform.db" in text),
            "kafka_topic_exact": "Topics                            amp.heartbeat" in text,
            "no_inline_credentials": not any(token in lowered for token in
                ("authorization ", "password ", "api_key ", "apikey ", "private key")),
        }
        db_line = next((line.strip()[3:].strip() for line in text.splitlines()
                        if line.strip().startswith("DB ")), "")
        checks["unique_database"] = bool(db_line and db_line not in db_paths)
        db_paths.add(db_line)
        records.append({"agent": agent, "file": name, "checks": checks,
                        "passed": all(checks.values())})
    return {"passed": all(item["passed"] for item in records), "records": records,
            "networkAttempted": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "config" / "amp")
    args = parser.parse_args(argv)
    try:
        report = validate_directory(args.config_dir)
    except (OSError, UnicodeError):
        report = {"passed": False, "error": "AMP_CONFIG_UNREADABLE", "networkAttempted": False}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
