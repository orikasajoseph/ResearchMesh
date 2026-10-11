"""Team-agreed business skills; not an enumeration defined by the AIP standard.

Do not silently map code_search to repository.analyze. Repository analysis has
more extensive inputs/outputs and is not supported by the v0.1 contract.
"""

from .contracts import ResearchTask

TASK_TO_SKILL = {
    "literature_search": "literature.search",
    "paper_read": "paper.analyze",
}
SKILL_TO_TASK = {value: key for key, value in TASK_TO_SKILL.items()}


def required_skill(task: ResearchTask) -> str:
    """Return the agreed discovery skill or fail explicitly for unbound tasks."""
    try:
        return TASK_TO_SKILL[task.taskType]
    except KeyError as exc:
        raise ValueError(
            f"No frozen discovery skill for taskType={task.taskType!r}. "
            "code_search is not repository.analyze; align the contract first."
        ) from exc
