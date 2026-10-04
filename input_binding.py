"""把直接上游结果写入业务 input；不发送网络请求，不编造论文。"""

from copy import deepcopy
from hashlib import sha256
import re
from urllib.parse import urlparse


class InputBindingError(ValueError):
    pass


def web_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username


def github_repository_url(value):
    """首期只接受明确的 HTTPS GitHub 仓库根地址，不做网络请求。"""
    if not isinstance(value, str):
        return False
    return re.fullmatch(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*/?", value) is not None


def paper_ref(data):
    """统一 PaperRef。只有标题不算可定位论文；ID 是本地引用，不是平台 artifact。"""
    if not isinstance(data, dict):
        raise InputBindingError("论文条目必须是对象")
    for key in ("paperId", "title"):
        if data.get(key) is not None and (not isinstance(data[key], str) or not data[key].strip()):
            raise InputBindingError(f"{key} 必须是非空字符串或 null")
    doi = data.get("doi")
    url, pdf = data.get("url"), data.get("pdfUrl")
    if doi is not None and (not isinstance(doi, str) or not doi.startswith("10.") or "/" not in doi):
        raise InputBindingError("DOI 格式无效")
    if any(value is not None and not web_url(value) for value in (url, pdf)):
        raise InputBindingError("论文 URL 必须是 HTTP(S) 地址")
    if not (doi or url or pdf):
        raise InputBindingError("论文缺少 DOI、url 或 pdfUrl，无法交给阅读 Agent")
    repos = data.get("repositoryUrls", [])
    if not isinstance(repos, list) or any(not web_url(value) for value in repos):
        raise InputBindingError("repositoryUrls 必须是 URL 数组")
    identity = doi or url or pdf
    return {
        "paperId": data.get("paperId") or "paper-" + sha256(identity.encode()).hexdigest()[:16],
        "title": data.get("title"), "doi": doi, "url": url, "pdfUrl": pdf,
        "repositoryUrls": list(dict.fromkeys(repos)),
    }


def _limit(constraints, key, default):
    value = constraints.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise InputBindingError(f"{key} 必须是 1—100 的整数")
    return value


def validate_task_input(task, *, dispatch=False):
    data, kind = task["input"], task["taskType"]
    allowed = {
        "literature_search": {"query"},
        "paper_read": {"papers"},
        "repository_analyze": {"papers", "paperNotes", "repositoryUrls"},
    }[kind]
    if set(data) - allowed:
        raise InputBindingError("input 含未约定字段；上游结果通过 dependsOn 绑定，不写占位字符串")
    if kind == "literature_search":
        if not isinstance(data.get("query"), str) or not data["query"].strip():
            raise InputBindingError("检索任务需要非空 query")
    else:
        papers = data.get("papers", [])
        if not isinstance(papers, list):
            raise InputBindingError("papers 必须是数组，不是上游任务编号或说明文字")
        for paper in papers:
            paper_ref(paper)
        if kind == "paper_read" and not papers and (dispatch or not task.get("dependsOn")):
            raise InputBindingError("阅读任务没有可定位论文或上游依赖")
        if kind == "repository_analyze":
            notes, urls = data.get("paperNotes", []), data.get("repositoryUrls", [])
            if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
                raise InputBindingError("paperNotes 必须是对象数组")
            if not isinstance(urls, list) or any(not github_repository_url(url) for url in urls):
                raise InputBindingError("repositoryUrls 必须是明确的 HTTPS GitHub 仓库根地址数组")
            if task["constraints"].get("allowRepositorySearch") is not False:
                raise InputBindingError("首期不支持自动查找仓库，必须设置 allowRepositorySearch=false")
            if task["constraints"].get("executeCode") is not False:
                raise InputBindingError("仓库静态分析必须明确设置 executeCode=false")
            if dispatch or not task.get("dependsOn"):
                if not urls:
                    raise InputBindingError("缺少仓库地址：请提供明确的 GitHub URL；首期不能根据论文自动查找")
                if task["constraints"].get("requirePaperNotes", False) and not notes:
                    raise InputBindingError("复现分析要求论文阅读结果，当前未提供")
    constraints = task["constraints"]
    for key in ("maxPapers", "maxResults", "maxRepositories"):
        if key in constraints:
            _limit(constraints, key, 3)
    for key in ("allowRepositorySearch", "requirePaperNotes", "executeCode"):
        if key in constraints and not isinstance(constraints[key], bool):
            raise InputBindingError(f"{key} 必须是布尔值")
    if constraints.get("executeCode", False):
        raise InputBindingError("v0.2 只支持静态代码分析，不能执行仓库代码")
    start, end = constraints.get("yearFrom"), constraints.get("yearTo")
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, int)) for v in (start, end)):
        raise InputBindingError("年份必须是整数")
    if start is not None and end is not None and start > end:
        raise InputBindingError("开始年份不能晚于结束年份")


def bind_task_input(task, dependency_results):
    """只读直接依赖；保留显式输入优先级，确定性按结果顺序截取，不声称做了智能选篇。"""
    bound = deepcopy(task)
    validate_task_input(bound)
    if bound["taskType"] == "literature_search":
        return bound
    data = bound["input"]
    upstream_papers, notes = [], []
    for dep in task.get("dependsOn", []):
        result = dependency_results.get(dep)
        if result is None:
            raise InputBindingError("缺少声明的上游结果")
        for item in result["items"]:
            if item["kind"] == "paper":
                upstream_papers.append(paper_ref(item["data"]))
            elif item["kind"] == "paper_note":
                notes.append(deepcopy(item["data"]))
                if item["data"].get("source"):
                    upstream_papers.append(paper_ref(item["data"]["source"]))
    refs = [paper_ref(p) for p in data.get("papers", [])] or upstream_papers
    unique = {}
    for ref in refs:
        key = ref["doi"] or ref["url"] or ref["pdfUrl"]
        unique.setdefault(key, ref)
    data["papers"] = list(unique.values())[:_limit(bound["constraints"], "maxPapers", 3)]
    if bound["taskType"] == "repository_analyze":
        selected = {p["paperId"] for p in data["papers"]}
        locators = {p["doi"] or p["url"] or p["pdfUrl"] for p in data["papers"]}
        def selected_note(note):
            if note.get("paperId") in selected:
                return True
            if not note.get("source"):
                return False
            ref = paper_ref(note["source"])
            return (ref["doi"] or ref["url"] or ref["pdfUrl"]) in locators
        data["paperNotes"] = data.get("paperNotes") or [
            note for note in notes if selected_note(note)
        ]
        urls = list(data.get("repositoryUrls", []))
        # 用户显式指定的仓库优先，不能被上游其他仓库替换或扩充。
        if not urls:
            for ref in data["papers"]:
                urls.extend(ref["repositoryUrls"])
        data["repositoryUrls"] = list(dict.fromkeys(urls))[:_limit(bound["constraints"], "maxRepositories", 3)]
        bound["constraints"].setdefault("executeCode", False)
    validate_task_input(bound, dispatch=True)
    return bound
