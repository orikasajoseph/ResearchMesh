"""显式迁移历史业务计划；不修改原对象，不猜测地址，不自动发任务。"""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from planner_contract import validate_plan


class MigrationError(ValueError):
    pass


def validate_legacy_plan(plan):
    candidate = deepcopy(plan)
    if not isinstance(candidate, dict) or candidate.get("schemaVersion") != "0.1":
        raise MigrationError("需要 schemaVersion=0.1 的历史计划")
    if not isinstance(candidate.get("tasks"), list):
        raise MigrationError("历史计划 tasks 必须是数组")
    for task in candidate["tasks"]:
        if not isinstance(task, dict):
            raise MigrationError("历史子任务必须是对象")
        if "requiredSkill" in task:
            if task.get("requiredCapability") != task["requiredSkill"]:
                raise MigrationError("新旧技能字段冲突")
            del task["requiredSkill"]
    schema = json.loads((Path(__file__).parent / "schemas/legacy/v0.1/plan.schema.json").read_text(encoding="utf-8"))
    if not Draft202012Validator(schema).is_valid(candidate):
        raise MigrationError("历史计划不符合 v0.1 外壳；请检查类型、字段和版本")
    seen = set()
    for task in candidate["tasks"]:
        if task["subtaskId"] in seen or any(dep not in seen for dep in task["dependsOn"]):
            raise MigrationError("历史任务编号或依赖无效")
        seen.add(task["subtaskId"])
    return candidate


def migrate_plan(plan):
    """返回新计划和迁移说明。失败时不产生可执行的新计划。"""
    if isinstance(plan, dict) and plan.get("schemaVersion") == "0.2":
        validate_plan(plan)
        return deepcopy(plan), []
    candidate = validate_legacy_plan(plan)
    candidate["schemaVersion"] = "0.2"
    notes = ["业务版本由 0.1 迁移到 0.2；requiredCapability 改为 requiredSkill。"]
    for task in candidate["tasks"]:
        task["schemaVersion"] = "0.2"
        task["requiredSkill"] = task.pop("requiredCapability")
        if task["taskType"] == "code_search":
            task["taskType"] = "repository_analyze"
            data = task["input"]
            urls = data.get("repositoryUrls", [])
            if not urls:
                raise MigrationError(f"{task['subtaskId']} 缺少仓库地址，不能将旧代码检索直接转换为仓库分析")
            if task["constraints"].get("executeCode", False) is not False:
                raise MigrationError("迁移不能放宽或静默改写 executeCode")
            search = task["constraints"].get("allowRepositorySearch", False)
            if not isinstance(search, bool):
                raise MigrationError("allowRepositorySearch 必须是布尔值")
            task["constraints"]["executeCode"] = False
            task["constraints"]["allowRepositorySearch"] = False
            notes.append(f"{task['subtaskId']} 改为仅分析已提供仓库；allowRepositorySearch=false，不执行旧检索目标。")
            task["goal"] = "静态分析已提供的 GitHub 仓库；不搜索其他仓库、不执行代码。原需求需人工核对：" + task["goal"]
    validate_plan(candidate)
    return candidate, notes
