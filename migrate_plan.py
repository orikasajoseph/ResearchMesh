"""显式将旧计划转换为新文件；不调用模型或 Agent，不覆盖原始记录。"""
import argparse
from datetime import datetime
import json
from pathlib import Path

from compatibility import migrate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="完整 Plan JSON；不接受 {plan, metadata} 包装")
    args = parser.parse_args()
    original = json.loads(args.source.read_text(encoding="utf-8"))
    plan, notes = migrate_plan(original)
    output = Path(__file__).parent / "outputs" / datetime.now().strftime("migration-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "migration.json").write_text(json.dumps({"source": str(args.source.resolve()), "notes": notes,
        "requiresHumanReview": bool(notes), "agentsExecuted": False, "aipUsed": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("迁移后请核对目标和约束，尚未执行任务。已保存：", output)


if __name__ == "__main__":
    main()
