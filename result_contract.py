"""共享模型的结果校验入口，另检查结果与请求的关联。"""

from typing import Any
import re

from common.models import ResearchResult, RESULT_FIELDS
from input_binding import paper_ref, web_url


def _validate_repository_record(data, task):
    """Validate Zhang's code_records.v1 without changing the shared envelope."""
    if not isinstance(data, dict):
        raise ValueError("INVALID_REPOSITORY_RECORD")
    repository = data.get("repository")
    if (not isinstance(repository, dict) or not web_url(repository.get("url"))
            or not isinstance(repository.get("commitHash"), str) or not repository["commitHash"]):
        raise ValueError("INVALID_REPOSITORY_IDENTITY")
    requested = task.get("input", {}).get("repositoryUrls", [])
    if not requested or repository["url"].rstrip("/") != requested[0].rstrip("/"):
        raise ValueError("REPOSITORY_RESULT_URL_MISMATCH")
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("REPOSITORY_EVIDENCE_REQUIRED")
    ids = set()
    for source in evidence:
        if not isinstance(source, dict):
            raise ValueError("INVALID_REPOSITORY_EVIDENCE")
        evidence_id = source.get("evidenceId")
        if (not isinstance(evidence_id, str) or not evidence_id
                or evidence_id in ids or not isinstance(source.get("filePath"), str)
                or not source["filePath"]):
            raise ValueError("INVALID_REPOSITORY_EVIDENCE")
        ids.add(evidence_id)
        for key in ("startLine", "endLine"):
            value = source.get(key)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("INVALID_REPOSITORY_EVIDENCE_LINE")
    for key in ("structure", "dependencies", "entrypoints", "runCommands", "reproductionBarriers"):
        if not isinstance(data.get(key), list):
            raise ValueError("INVALID_REPOSITORY_RECORD")
    if not data["structure"]:
        raise ValueError("REPOSITORY_STATIC_RESULT_INCOMPLETE")
    for file in data["structure"]:
        if not isinstance(file, dict) or not file.get("filePath") or not isinstance(file.get("components"), list):
            raise ValueError("INVALID_REPOSITORY_STRUCTURE")
        for component in file["components"]:
            if not isinstance(component, dict) or component.get("evidenceId") not in ids:
                raise ValueError("UNSUPPORTED_REPOSITORY_EVIDENCE")
    for key in ("dependencies", "entrypoints", "runCommands", "reproductionBarriers"):
        for item in data[key]:
            if (not isinstance(item, dict) or not isinstance(item.get("evidenceIds"), list)
                    or not item["evidenceIds"] or not set(item["evidenceIds"]) <= ids):
                raise ValueError("UNSUPPORTED_REPOSITORY_EVIDENCE")
    overview = data.get("overview")
    if overview is not None and (not isinstance(overview, dict)
                                or not isinstance(overview.get("evidenceIds"), list)
                                or not set(overview["evidenceIds"]) <= ids):
        raise ValueError("UNSUPPORTED_REPOSITORY_EVIDENCE")
    from aip_repository_partner import repository_request
    expected_paper_evidence = {
        item.evidenceId: item.model_dump(mode="json")
        for note in repository_request(task).input.paperNotes for item in note.evidence
    }
    paper_evidence = data.get("paperEvidence", [])
    relations = data.get("paperCodeRelations", [])
    if not isinstance(paper_evidence, list) or not isinstance(relations, list):
        raise ValueError("INVALID_PAPER_CODE_RELATION")
    paper_ids = set()
    for item in paper_evidence:
        if not isinstance(item, dict):
            raise ValueError("UNSUPPORTED_PAPER_EVIDENCE")
        evidence_id = item.get("evidenceId")
        if (not isinstance(evidence_id, str) or evidence_id in paper_ids
                or item != expected_paper_evidence.get(evidence_id)):
            raise ValueError("UNSUPPORTED_PAPER_EVIDENCE")
        paper_ids.add(evidence_id)
    for relation in relations:
        if (not isinstance(relation, dict) or not isinstance(relation.get("claim"), str)
                or not relation["claim"].strip()
                or re.search(r"\b(?:executed|ran|reproduced|replicated)\b|已(?:执行|运行|复现)|成功复现",
                             relation["claim"], re.IGNORECASE)
                or not isinstance(relation.get("paperEvidenceIds"), list)
                or not relation["paperEvidenceIds"]
                or not set(relation["paperEvidenceIds"]) <= paper_ids
                or not isinstance(relation.get("codeEvidenceIds"), list)
                or not relation["codeEvidenceIds"]
                or not set(relation["codeEvidenceIds"]) <= ids):
            raise ValueError("UNSUPPORTED_PAPER_CODE_RELATION")


def validate_result(result: dict[str, Any], task: dict[str, Any]) -> None:
    parsed = ResearchResult.model_validate(result)
    if parsed.schemaVersion != task["schemaVersion"]:
        raise ValueError("结果 schemaVersion 与请求不一致")
    if parsed.subtaskId != task["subtaskId"]:
        raise ValueError("结果 subtaskId 与请求不一致")
    if parsed.taskType != task["taskType"]:
        raise ValueError("结果 taskType 与请求不一致")
    expected_kind = {"literature_search": "paper", "paper_read": "paper_note", "repository_analyze": "repository"}[parsed.taskType]
    for item in parsed.items:
        if item.kind != expected_kind and not (parsed.taskType == "repository_analyze" and item.kind == "repository_record"):
            raise ValueError("结果条目类型与任务不一致")
        data = item.data
        if item.kind == "repository_record":
            _validate_repository_record(data, task)
            continue
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
