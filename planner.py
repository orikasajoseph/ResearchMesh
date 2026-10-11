"""ResearchMesh 的最小 Planner：调用模型、解析 JSON、校验内部计划。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

from planner_contract import validate_plan


def check_repository_provenance(plan, query):
    """Planner 尚未调用检索工具，只能携带用户已给出的仓库地址。"""
    def canonical(url):
        return url.rstrip('/').removesuffix('.git').lower()
    supplied = {canonical(url) for url in re.findall(
        r'https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*/?', query)}
    for task in plan['tasks']:
        data = task['input']
        urls = list(data.get('repositoryUrls', []))
        for paper in data.get('papers', []):
            urls.extend(paper.get('repositoryUrls', []))
        for note in data.get('paperNotes', []):
            source = note.get('source')
            if isinstance(source, dict):
                source_urls = source.get('repositoryUrls', [])
                if not isinstance(source_urls, list):
                    raise ValueError('论文笔记来源中的 repositoryUrls 必须是数组')
                urls.extend(source_urls)
        if any(not isinstance(url, str) or canonical(url) not in supplied for url in urls):
            raise ValueError('Planner 不能补造用户未提供的仓库地址；上游地址只能在执行阶段绑定')


PLANNER_INSTRUCTIONS = """你是 ResearchMesh Leader 内部的 Planner。请把用户研究需求规划为任务。
只输出一个 JSON 对象，不要 Markdown、代码围栏或解释。
顶层字段：schemaVersion="0.2"、userQuery（原样复制用户输入）、
needsClarification（布尔值）、clarificationQuestion（字符串或 null）、tasks（数组）。
如果研究主题不明确，needsClarification=true，给出一个具体追问，tasks=[]。
否则 needsClarification=false，clarificationQuestion=null，并只生成用户确实需要的任务。
任务类型只能是 literature_search、paper_read、repository_analyze。
每项任务必须包含：schemaVersion="0.2"、subtaskId、taskType、goal、input（对象）、
constraints（对象）、outputSchema、requiredSkill、dependsOn（数组）。
requiredSkill 分别为 literature.search、paper.analyze、repository.analyze，
这是业务 skill，不是 AIP 技术 capabilities；subtaskId 依次为 S1、S2……。
outputSchema 分别使用 paper_records.v1、paper_notes.v1、code_records.v1。
dependsOn 只引用前面已有的任务。例如先检索、再读找到的论文，就让阅读任务依赖检索任务。
保留用户的年份、篇数等限制。不要默认把所有需求拆成三个任务。
不要指定具体 Agent、AIC 或服务地址；可以保留用户提供的论文/仓库 URL，不能编造。
literature_search 的 input 只含 query；paper_read 的 input 只含 papers 数组；
repository_analyze 的 input 只含 papers、paperNotes、repositoryUrls 数组。
PaperRef 字段是 paperId、title、doi、url、pdfUrl、repositoryUrls；未提供的信息可省略，
但具体论文必须有 doi、url 或 pdfUrl。未检索出的论文使用 papers=[]，并声明 dependsOn，
Leader 执行时负责填入；禁止使用“来自 S1”等字符串代替数组。
constraints 使用 yearFrom、yearTo、maxResults、maxPapers、maxRepositories 表示数量时间限制。
仓库只做静态分析，constraints 必须明确 executeCode=false、allowRepositorySearch=false。
首期不支持根据 DOI、论文标题或阅读笔记搜索仓库。
仅要求分析仓库但未提供地址时，needsClarification=true，追问 GitHub 仓库 URL，tasks=[]。
有检索或阅读上游时，repositoryUrls 可暂为空并声明 dependsOn，但仓库分析是有条件执行：
goal 必须写明仅在上游实际提供仓库 URL 时分析，否则提示缺少仓库地址；不能承诺自动找到仓库。
用户提供的仓库 URL 应放在 repositoryUrls 中，不得替换或追加臆造地址。
需要结合论文分析复现难度时 requirePaperNotes=true，并直接依赖 paper_read；
仅做仓库结构分析不一定依赖阅读任务。不要为仅阅读指定论文或仅分析指定仓库增加无关搜索。
这里产出的是 Leader 内部计划，不是 AIP 消息。"""


class PlanningModel(Protocol):
    async def generate(self, instructions: str, user_query: str) -> str:
        """返回模型生成的 JSON 文本；已由 DeepSeek 适配器实现。"""


class PlannerError(Exception):
    """模型响应无法变成合格的内部计划。"""


class Planner:
    def __init__(self, model: PlanningModel, *, max_attempts: int = 2, timeout_seconds: float = 30):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts not in (1, 2):
            raise ValueError("max_attempts 只能为 1 或 2（最多修正一次）")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.model = model
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds

    async def plan(self, user_query: str) -> dict[str, Any]:
        if not isinstance(user_query, str) or not user_query.strip():
            raise ValueError("请输入研究问题")
        if len(user_query) > 6000:
            raise ValueError("研究问题不能超过 6000 个字符")

        instructions = PLANNER_INSTRUCTIONS
        for attempt in range(self.max_attempts):
            try:
                raw = await asyncio.wait_for(
                    self.model.generate(instructions, user_query), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                raise PlannerError("模型调用超时；没有生成可用计划") from exc
            except Exception as exc:
                # 不在界面或日志中回显提供商异常，以免泄漏未来接口的认证信息。
                raise PlannerError("模型调用失败；请检查模型适配器配置") from exc
            try:
                return self._parse(raw, user_query)
            except PlannerError:
                if attempt + 1 == self.max_attempts:
                    raise
                instructions = PLANNER_INSTRUCTIONS + (
                    "\n上次输出未通过 JSON 或计划校验。请重新生成完整 JSON，严格遵守以上字段、"
                    "类型和依赖约束，原样保留 userQuery；不要输出解释或代码围栏。"
                )
        raise PlannerError("没有生成可用计划")

    @staticmethod
    def _parse(raw: str, user_query: str) -> dict[str, Any]:
        try:
            result = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlannerError("模型没有返回合法 JSON") from exc

        if not isinstance(result, dict):
            raise PlannerError("模型返回的顶层必须是 JSON 对象")
        if result.get("userQuery") != user_query:
            raise PlannerError("模型没有原样保留用户输入")
        try:
            validate_plan(result)
            check_repository_provenance(result, user_query)
        except ValueError as exc:
            raise PlannerError(f"模型返回的计划不合格：{exc}") from exc
        return result
