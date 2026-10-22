"""Check local Leader credentials; --execute opts into one real MQ connection."""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from platform_mq_check import ROOT, check_connection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aic", required=True, help="Actual registered Leader AIC; not an API key")
    parser.add_argument("--credentials-dir", required=True, type=Path,
                        help="External directory containing leader-cert.pem/leader-key.pem/trust-bundle.pem")
    parser.add_argument("--execute", action="store_true", help="One real AMQPS connection, no groups/tasks")
    parser.add_argument("--timeout", type=float, default=20, help="Connect/channel deadline, >0 and <=60 seconds")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "platform-mq")
    args = parser.parse_args(argv)
    # Do not enable raw SDK/transport debug logs, which can expose local context.
    logging.basicConfig(level=logging.CRITICAL)
    record = asyncio.run(check_connection(
        aic=args.aic, credentials_dir=args.credentials_dir,
        execute=args.execute, timeout=args.timeout,
    ))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"mq-check-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("记录：", path)
    print("仅检查证书/连接；未建群、未调用 Partner，未验证 AIP 任务或科研业务。")
    if record["error"] and record["error"]["code"] == "OPTIONAL_DEPENDENCY_MISSING":
        print("请在指定工具环境安装 requirements-mq-check.txt；不要覆盖现有环境。")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
