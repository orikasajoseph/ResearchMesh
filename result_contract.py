"""共享模型的结果校验入口，另检查结果与请求的关联。"""

from typing import Any

from common.models import ResearchResult, RESULT_FIELDS
from input_binding import paper_ref, web_url


def validate_result(result: dict[str, Any], task: dict[str, Any]) -> None:
    parsed = ResearchResult.model_validate(result)
    if parsed.subtaskId != task["subtaskId"]:
        raise ValueError("结果 subtaskId 与请求不一致")
    if parsed.taskType != task["taskType"]:
        raise ValueError("结果 taskType 与请求不一致")
    expected_kind = {"literature_search": "paper", "paper_read": "paper_note", "code_search": "repository"}[parsed.taskType]
    for item in parsed.items:
        if item.kind != expected_kind:
            raise ValueError("结果条目类型与任务不一致")
        data = item.data
        if item.kind == "paper":
            paper_ref(data)
            if not isinstance(data.get("title"), str) or not data["title"].strip():
                raise ValueError("论文结果必须有标题")
        elif item.kind == "paper_note":
            paper_ref(data.get("source"))
            for key in ("method", "findings", "limitations"):
                if not isinstance(data.get(key), str) or not data[key].strip():
                    raise ValueError("论文阅读缺少方法、发现或局限字段；未知请明确说明")
            if data.get("isMock") is not True and data.get("analysisLevel") not in ("abstract", "fulltext"):
                raise ValueError("真实论文阅读必须说明 abstract/fulltext，不能把摘要当全文")
        else:
            if not web_url(data.get("url")):
                raise ValueError("仓库结果缺少有效 URL")
            if not isinstance(data.get("license"), str) or not data["license"].strip():
                raise ValueError("仓库结果必须记录许可证或明确 UNKNOWN")
            if not isinstance(data.get("relationEvidence"), str) or not data["relationEvidence"].strip():
                raise ValueError("仓库结果必须说明论文关联依据；无关联请说明不适用")
        if data.get("isMock") is not True and item.kind != "paper":
            evidence = data.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("真实分析必须提供 evidence 来源数组")
            for source in evidence:
                if not isinstance(source, dict) or not web_url(source.get("url")):
                    raise ValueError("证据必须包含有效 URL")
                for key in ("sourceId", "locator", "supports"):
                    if not isinstance(source.get(key), str) or not source[key].strip():
                        raise ValueError("证据缺少 sourceId、locator 或 supports")
