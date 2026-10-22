"""Fresh three-agent continuation of the existing single-entry local AIP CLI.

Uses the already verified stage programs, not another Planner or protocol.
This is local loopback AIP, never a claim of ADP/AMQP/platform acceptance.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sys

from aip_reader_inputs import read_json, prepare_reader_input
from assemble_aip_evidence import assemble, check_reader_wire
from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input, github_repository_url
from planner import check_repository_provenance
from planner_contract import validate_plan
from reader_repository_handoff import check_reader_products
from repository_paper_context import prepare_repository_task
from repository_resource import SCOPE as RESOURCE_SCOPE
from result_contract import validate_result
from run_aip_business import save_json
from run_aip_literature import assess_live_run
from run_aip_repository import assess, assess_enhanced, render_report, render_enhanced_report

SCOPE = "LOCAL_AIP_SINGLE_ENTRY_THREE_AGENTS_V1"


def triple(plan):
    """Reject unsupported plans before executing search or paid Reader."""
    validate_plan(plan)
    check_repository_provenance(plan, plan["userQuery"])
    if plan["needsClarification"]:
        raise ValueError("PLAN_NEEDS_CLARIFICATION")
    tasks = plan["tasks"]
    if len(tasks) != 3 or [t["taskType"] for t in tasks] != ["literature_search", "paper_read", "repository_analyze"]:
        raise ValueError("SEARCH_READ_REPOSITORY_CHAIN_REQUIRED")
    search, reader, repository = tasks
    if (search["dependsOn"] or reader["dependsOn"] != [search["subtaskId"]]
            or reader["constraints"].get("maxPapers") != 1 or reader["input"].get("papers")
            or reader["subtaskId"] not in repository["dependsOn"]
            or repository["constraints"].get("maxRepositories", 1) != 1
            or repository["constraints"].get("requirePaperNotes") is not True
            or repository["input"].get("papers") or repository["input"].get("paperNotes")
            or len(repository["input"].get("repositoryUrls", [])) > 1):
        raise ValueError("SINGLE_PAPER_REPOSITORY_DEPENDENCY_REQUIRED")
    return tuple(t["subtaskId"] for t in tasks)


def verify_frozen(output, state):
    for name, digest in state.get("verifiedFiles", {}).items():
        path = (output / name).resolve()
        if not path.is_relative_to(output.resolve()) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("THREE_AGENT_CHECKPOINT_CHANGED")


def freeze(output, state, paths):
    for path in paths:
        name = str(path.resolve().relative_to(output.resolve()))
        digest = sha256(path.read_bytes()).hexdigest()
        previous = state.setdefault("verifiedFiles", {}).get(name)
        if previous and previous != digest:
            raise ValueError("THREE_AGENT_CHECKPOINT_CHANGED")
        state["verifiedFiles"][name] = digest


def write_once(path, value):
    if path.exists():
        if read_json(path) != value:
            raise ValueError("THREE_AGENT_CHECKPOINT_CHANGED")
    else:
        save_json(path, value)


def validate_resource(resource, request):
    if resource.get("scope") != RESOURCE_SCOPE or resource.get("request") != request or resource.get("modelCalls") != 0:
        raise ValueError("INVALID_REPOSITORY_RESOURCE_PROOF")
    if resource.get("state") != "selected":
        raise ValueError("VERIFIED_REPOSITORY_URL_UNAVAILABLE")
    urls = resource.get("repositoryUrls", [])
    if len(urls) != 1 or not github_repository_url(urls[0]):
        raise ValueError("INVALID_REPOSITORY_RESOURCE_PROOF")
    if request["explicitRepositoryUrls"]:
        valid = resource.get("selectionMode") == "user_provided" and urls == request["explicitRepositoryUrls"]
    else:
        proof = resource.get("proof", {})
        valid = (resource.get("selectionMode") == "verified_public_readme" and resource.get("verifiedRelation") is True
                 and proof.get("repositoryUrl") == urls[0] and proof.get("titleMatched") is True
                 and proof.get("identifierMatched") is True
                 and proof.get("evidenceKind") == "public_github_readme_title_and_paper_identifier")
    if not valid:
        raise ValueError("INVALID_REPOSITORY_RESOURCE_PROOF")
    return urls


def bind_repository(plan, prefix, resource):
    original = plan["tasks"][2]
    results = {r["subtaskId"]: deepcopy(r["result"]) for r in prefix["executionRecords"]}
    reader = prefix["executionRecords"][1]
    notes = [item["data"] for item in reader["result"]["items"] if item["kind"] == "paper_note"]
    if len(notes) != 1:
        raise ValueError("SINGLE_FULLTEXT_PAPER_CONTEXT_REQUIRED")
    before = read_json(Path(reader["recordDirectory"]) / "reader_input.json")["paperBeforePdfResolution"]
    search_data = next(item["data"] for item in prefix["results"][0]["items"]
                       if item["kind"] == "paper" and item["data"].get("paperId") == before.get("paperId"))
    request = {"paper": deepcopy(notes[0]["source"]), "searchData": deepcopy(search_data),
               "explicitRepositoryUrls": deepcopy(original["input"].get("repositoryUrls", []))}
    if resource is None:
        return request
    urls = validate_resource(resource, request)
    seed = deepcopy(original)
    seed["input"]["repositoryUrls"] = urls
    bound = bind_task_input(seed, results)
    prepare_repository_task(bound)  # fulltext, concrete evidence, source matching, safety flags
    submitted = deepcopy(bound)
    submitted["dependsOn"] = []  # only this already-resolved subtask goes to the stage executor
    single = {key: deepcopy(plan[key]) for key in ("schemaVersion", "userQuery", "needsClarification", "clarificationQuestion")}
    single["tasks"] = [submitted]
    validate_plan(single)
    return {"originalDependentTask": deepcopy(original), "boundTask": bound, "plan": single,
            "readerAipTaskId": reader["aipTaskId"], "resource": deepcopy(resource),
            "dependenciesVerifiedComplete": list(original["dependsOn"])}


def search_prefix(directory, plan):
    outcome = read_json(directory / "outcome.json")
    backend = read_json(directory / "backend_outcome.json")
    payload = {key: deepcopy(plan["tasks"][0][key]) for key in BUSINESS_TASK_FIELDS}
    if read_json(directory / "payload.json") != payload or not assess_live_run(outcome, backend)["businessReady"]:
        raise ValueError("SEARCH_BUSINESS_CHECK_FAILED")
    check_reader_wire(outcome, payload)
    result = outcome["execution"]["result"]
    check_reader_products(outcome, result)
    validate_result(result, payload)
    execution = outcome["execution"]
    row = {"subtaskId": payload["subtaskId"], "requiredSkill": "literature.search", "dependsOn": [],
           "aipTaskId": execution["taskId"], "agentAic": execution["agentAic"], "status": "completed",
           "result": deepcopy(result), "recordDirectory": str(directory)}
    return {"executionRecords": [row], "results": [deepcopy(result)]}


def repository_record(directory, binding, mode):
    outcome = read_json(directory / "outcome.json")
    backend = read_json(directory / "backend_outcome.json")
    payload = {key: deepcopy(binding["boundTask"][key]) for key in BUSINESS_TASK_FIELDS}
    if read_json(directory / "repository_input.json") != payload or read_json(directory / "plan.json") != binding["plan"]:
        raise ValueError("REPOSITORY_BOUND_INPUT_MISMATCH")
    acceptance = (assess if mode == "static" else assess_enhanced)(outcome, backend, payload)
    # Static results may survive failed optional enrichment. Never label that enhancement success.
    if not acceptance["businessReady"]:
        raise ValueError("REPOSITORY_BUSINESS_CHECK_FAILED")
    row = outcome["executionRecords"][0]
    execution = row.get("aipReceipt", {}).get("execution")
    if not execution or execution.get("result") != outcome["results"][0]:
        raise ValueError("REPOSITORY_AIP_RECEIPT_REQUIRED")
    wire = {"execution": execution, "aipMessages": outcome["aipMessages"]}
    check_reader_wire(wire, payload)
    check_reader_products(wire, outcome["results"][0])
    validate_result(outcome["results"][0], payload)
    original = binding["originalDependentTask"]
    return {"subtaskId": original["subtaskId"], "requiredSkill": original["requiredSkill"],
            "dependsOn": original["dependsOn"], "aipTaskId": execution["taskId"],
            "agentAic": execution["agentAic"], "status": "completed", "result": outcome["results"][0],
            "payload": payload, "recordDirectory": str(directory), "acceptance": acceptance}, outcome


def render_three(record, repo_report=""):
    completed = record.get("acceptance", {}).get("allThreeBusinessReady", False)
    lines = ["# ResearchMesh 三智能体科研报告", "",
             "> " + ("本机 AIP 三阶段业务已完成。" if completed else "流程尚未完整完成；以下仅列出已验证结果。"),
             "> 未使用正式 ADP/梧桐/AMQP，未验证证书身份；没有执行目标仓库代码或证明复现成功。",
             "> Planner、全文阅读和可选仓库增强可能计费；本报告的整理本身不额外调用模型。", "",
             "研究问题：" + record.get("userQuery", ""), "",
             "规划来源：" + ("本轮调用 Planner" if record.get("plannerExecutedByPipeline") else "复用保存计划"), "",
             "| 子任务 | 能力 | 依赖 | 状态 |", "|---|---|---|---|"]
    rows = {row["subtaskId"]: row for row in record.get("executionRecords", [])}
    for task in record["plan"]["tasks"]:
        row = rows.get(task["subtaskId"])
        lines.append("| " + " | ".join([task["subtaskId"], task["requiredSkill"],
                     ", ".join(task["dependsOn"]) or "无", row["status"] if row else "未完成/未验证"]) + " |")
    for row in record.get("executionRecords", []):
        if row["requiredSkill"] == "repository.analyze":
            continue
        lines += ["", "## " + row["subtaskId"], "", row["result"]["summary"], ""]
        for item in row["result"]["items"]:
            data = item["data"]
            if item["kind"] == "paper":
                lines += ["- " + str(data.get("title") or "未提供标题") + "；" + str(data.get("url") or data.get("doi") or "无链接")]
            elif item["kind"] == "paper_note":
                lines += ["### " + str(data["source"].get("title") or data["source"]["paperId"]), "",
                          "方法：" + data["method"], "", "发现：" + data["findings"], "",
                          "局限：" + data["limitations"], "", "原文证据（需人工核验）：", ""]
                lines += ["- [" + e["locator"] + "](" + e["url"] + ")：" + e["supports"] for e in data["evidence"]]
    resource = record.get("repositoryResource")
    if resource:
        lines += ["", "## 仓库地址来源", "", "选择方式：" + resource.get("selectionMode", "未找到"),
                  "；".join(resource.get("repositoryUrls", [])) or "没有可靠仓库地址；不猜测。",
                  "用户指定地址不等于论文与仓库关联已核验；自动匹配也不证明官方作者身份。", ""]
    if repo_report:
        lines += ["", "## S3 仓库分析与论文对照", "", repo_report]
    if record.get("blocker"):
        descriptions = {
            "VERIFIED_REPOSITORY_URL_UNAVAILABLE": "没有找到能核验论文身份的仓库地址。已保留论文与阅读结果，未运行 S3。",
            "SEARCH_BUSINESS_CHECK_FAILED": "检索未提供可验收的论文记录，或记录与实际 AIP/后台结果不一致。",
            "STAGE_FAILED_PDF": "未获得已知来源、标题与缓存哈希都通过校验的 PDF；未冒充全文已读。",
            "STAGE_FAILED_READER": "全文阅读阶段未成功，具体固定错误码见 reader/ 中的记录；不会自动重复付费。",
            "STAGE_FAILED_REPOSITORY": "仓库分析未成功，已保留前面的全文笔记和证据。",
        }
        lines += ["", "## 未完成原因", "", record["blocker"], "",
                  descriptions.get(record["blocker"], "请检查对应阶段记录，不能将此报告作为完整成功证明。"), "",
                  "已完成记录保留。不会自动重跑失败/中断的收费阶段；请先检查日志。"]
    return "\n".join(lines) + "\n"


def save_partial(output, plan, state, prefix=None, *, blocker=None, resource=None):
    value = {"scope": SCOPE, "userQuery": plan["userQuery"], "plan": deepcopy(plan),
             "plannerExecutedByPipeline": state["stages"].get("planner", {}).get("state") == "completed",
             "executionRecords": deepcopy((prefix or {}).get("executionRecords", [])),
             "repositoryResource": resource, "blocker": blocker,
             "acceptance": {"allThreeBusinessReady": False, "allTasksInSavedPlanAccountedFor": False,
                            "platformDiscoveryUsed": False, "scientificClaimsHumanVerified": False}}
    save_json(output / "research_run.json", value)
    (output / "report.md").write_text(render_three(value), encoding="utf-8")
    return value


def execute_three(output, state, args, plan, *, root, stage, persist, runner):
    search_id, reader_id, repository_id = triple(plan)
    config = {name: str(getattr(args, name).resolve()) for name in
              ("reader_root", "reader_python", "wrapper_root", "repository_root", "repository_python")}
    config.update(analysisMode=args.repository_analysis_mode, llmAdapter=args.llm_adapter)
    if args.pdf_proof:
        config["providedPdfProofSha256"] = sha256(args.pdf_proof.read_bytes()).hexdigest()
    if state.get("threeAgentConfig") and state["threeAgentConfig"] != config:
        raise ValueError("THREE_AGENT_CONFIG_CHANGED")
    state["threeAgentConfig"] = config
    verify_frozen(output, state)
    persist(output, state)
    py = [sys.executable, "-X", "utf8"]
    prefix, resource = None, None
    save_partial(output, plan, state)
    try:
        search = output / "literature"
        stage(output, state, "literature", py + [str(root / "run_aip_literature.py"), "--plan", str(output / "plan.json"),
              "--subtask", search_id, "--wrapper-root", config["wrapper_root"], "--record-dir", str(search), "--execute"],
              timeout=190, runner=runner)
        # Search protocol and data get independently checked by prepare_reader_input.
        freeze(output, state, [search / n for n in ("plan.json", "payload.json", "outcome.json", "backend_outcome.json")])
        persist(output, state)
        if read_json(search / "plan.json") != plan:
            raise ValueError("THREE_AGENT_CHECKPOINT_CHANGED")
        prefix = search_prefix(search, plan)
        save_partial(output, plan, state, prefix)
        proof = output / "pdf_resolution.json"
        if args.pdf_proof:
            write_once(proof, read_json(args.pdf_proof))
        else:
            stage(output, state, "pdf", [config["reader_python"], "-X", "utf8", str(root / "aip_pdf_worker.py"),
                  "--source", str(search), "--output", str(proof), "--cache", str(root / "outputs/pdf-cache"),
                  "--search-id", search_id, "--reader-id", reader_id, "--execute"], timeout=240, runner=runner)
        prepared = prepare_reader_input(search, proof, cache_dir=root / "outputs/pdf-cache", subtask_id=reader_id)
        freeze(output, state, [proof]); persist(output, state)
        reader = output / "reader"
        stage(output, state, "reader", py + [str(root / "run_aip_reader.py"), "--source", str(search),
              "--pdf-proof", str(proof), "--subtask", reader_id, "--reader-root", config["reader_root"],
              "--reader-python", config["reader_python"], "--record-dir", str(reader), "--execute"], timeout=780, runner=runner)
        prefix = assemble(search, reader, cache_dir=root / "outputs/pdf-cache", planned_repository=True)
        check_reader_products(read_json(reader / "outcome.json"), prefix["results"][1])
        if read_json(reader / "reader_input.json") != prepared:
            raise ValueError("THREE_AGENT_CHECKPOINT_CHANGED")
        freeze(output, state, [reader / n for n in ("reader_input.json", "reader_journal.json", "outcome.json")])
        persist(output, state)
        save_partial(output, plan, state, prefix)
        request = bind_repository(plan, prefix, None)
        write_once(output / "repository_resource_input.json", request)
        stage(output, state, "resources", py + [str(root / "repository_resource.py"), "--input",
              str(output / "repository_resource_input.json"), "--output", str(output / "repository_resource.json"),
              "--execute"], timeout=100, runner=runner)
        resource = read_json(output / "repository_resource.json")
        binding = bind_repository(plan, prefix, resource)
        write_once(output / "repository_binding.json", binding)
        write_once(output / "repository_plan.json", binding["plan"])
        freeze(output, state, [output / n for n in ("repository_resource_input.json", "repository_resource.json",
                                                   "repository_binding.json", "repository_plan.json")])
        persist(output, state)
        repository = output / "repository"
        command = py + [str(root / "run_aip_repository.py"), "--plan", str(output / "repository_plan.json"),
                       "--wrapper-root", config["repository_root"], "--repository-python", config["repository_python"],
                       "--analysis-mode", config["analysisMode"], "--record-dir", str(repository), "--execute"]
        if config["analysisMode"] == "enhanced":
            command += ["--env-file", str(root / ".env")]
        # An enrichment failure can still have valid static output. Do not replay;
        # assess saved output separately and explicitly report degraded success.
        reusable_degradation = (state.get("state") == "completed_with_warnings"
            and config["analysisMode"] == "enhanced"
            and state["stages"].get("repository", {}).get("state") == "failed"
            and all(str(Path("repository") / n) in state.get("verifiedFiles", {}) for n in
                    ("plan.json", "repository_input.json", "outcome.json", "backend_outcome.json")))
        if not reusable_degradation:
            try:
                stage(output, state, "repository", command, timeout=410, runner=runner)
            except ValueError as error:
                if str(error) != "STAGE_FAILED_REPOSITORY" or config["analysisMode"] != "enhanced":
                    raise
        row, repo_outcome = repository_record(repository, binding, config["analysisMode"])
        ids = [r["aipTaskId"] for r in prefix["executionRecords"]] + [row["aipTaskId"]]
        if len(set(ids)) != 3 or row["subtaskId"] != repository_id:
            raise ValueError("DISTINCT_AIP_TASK_IDS_REQUIRED")
        freeze(output, state, [repository / n for n in ("plan.json", "repository_input.json", "outcome.json", "backend_outcome.json")])
        value = save_partial(output, plan, state, prefix, resource=resource)
        value["executionRecords"].append(row)
        value["sourceManifest"] = [{"path": name, "sha256": digest} for name, digest in state["verifiedFiles"].items()]
        value["acceptance"].update(allThreeBusinessReady=True, allTasksInSavedPlanAccountedFor=True,
                                    singleFreshEndToEndRun=value["plannerExecutedByPipeline"],
                                    allThreeAipTasksCompleted=True, repositoryAnalysisMode=config["analysisMode"],
                                    repositoryEnhancementSucceeded=row["acceptance"].get("enhancementSucceeded", False),
                                    targetCodeExecuted=False, reproductionVerified=False)
        renderer = render_report if config["analysisMode"] == "static" else render_enhanced_report
        save_json(output / "research_run.json", value)
        (output / "report.md").write_text(render_three(value, renderer(repo_outcome, row["acceptance"])), encoding="utf-8")
        state.update(state="completed" if row["acceptance"]["passed"] else "completed_with_warnings")
        state.setdefault("finishedAt", datetime.now().astimezone().isoformat())
        persist(output, state)
        return value
    except (Exception, KeyboardInterrupt) as error:
        from run_aip_research import failure_code
        # Existing report has useful validated prefix, never raw exceptions/keys.
        save_partial(output, plan, state, prefix, blocker=failure_code(error), resource=resource)
        raise
