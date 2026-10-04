"""ResearchMesh 业务 skill；不是 AIP 协议 capabilities。"""

SKILLS = {
    "literature_search": "literature.search",
    "paper_read": "paper.analyze",
    "code_search": "repository.analyze",
}
OUTPUT_SCHEMAS = {
    "literature_search": "paper_records.v1",
    "paper_read": "paper_notes.v1",
    "code_search": "code_records.v1",
}


def canonical_skill(value):
    # 仅兼容旧本地 Mock 注册表；新 Planner 必须输出点分 skill。
    return SKILLS.get(value, value)
