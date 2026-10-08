import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


LLM_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from common.llm.researchmesh_llm import LLMError, LLMProvider  # noqa: E402


EXAMPLES_DIR = LLM_ROOT / "examples"


def _write_json(path: Path, data: dict) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(serialized)
    path.write_text(serialized, encoding="utf-8")


def main() -> int:
    api_key_configured = bool(os.getenv("DEEPSEEK_API_KEY"))
    if not api_key_configured:
        print("DEEPSEEK_API_KEY is not configured")
        return 0

    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    provider = LLMProvider()

    chat_response = provider.chat([
        {"role": "system", "content": "You are a research assistant."},
        {"role": "user", "content": "请用一句话解释什么是强化学习。"},
    ])
    _write_json(
        EXAMPLES_DIR / "llm_chat_success.json",
        chat_response.model_dump(mode="json"),
    )

    structured_messages = [
        {
            "role": "system",
            "content": "你是 ResearchMesh 的科研任务规划器。",
        },
        {
            "role": "user",
            "content": (
                "请把下面科研请求转换成 JSON："
                "‘调研 AlphaEvolve 的研究进展并分析复现难度。’"
                "只输出 topic、keywords、requiredSkills；requiredSkills 必须是 "
                "literature.search、paper.analyze、repository.analyze。"
            ),
        },
    ]
    expected_schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "requiredSkills": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["topic", "keywords", "requiredSkills"],
        "additionalProperties": False,
    }
    _write_json(
        EXAMPLES_DIR / "llm_structured_input.json",
        {"messages": structured_messages, "schema": expected_schema},
    )
    structured_output = provider.generate_json(
        structured_messages, schema=expected_schema
    )
    if set(structured_output) != {"topic", "keywords", "requiredSkills"}:
        raise ValueError("Structured output contains unexpected fields")
    if not isinstance(structured_output["topic"], str):
        raise ValueError("Structured output topic is invalid")
    if not isinstance(structured_output["keywords"], list):
        raise ValueError("Structured output keywords are invalid")
    expected_skills = {
        "literature.search", "paper.analyze", "repository.analyze"
    }
    if set(structured_output["requiredSkills"]) != expected_skills:
        raise ValueError("Structured output requiredSkills are invalid")
    _write_json(
        EXAMPLES_DIR / "llm_structured_output.json", structured_output
    )

    print(chat_response.content)
    print(f"model={chat_response.model}")
    print(f"latencyMs={chat_response.latencyMs}")
    usage = chat_response.usage.model_dump() if chat_response.usage else None
    print("usage=" + json.dumps(usage, ensure_ascii=False))
    print("structuredJsonValid=true")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LLMError as exc:
        print(f"DeepSeek live test failed: {exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("DeepSeek live test interrupted")
        raise SystemExit(130)
    except Exception as exc:
        print(f"DeepSeek live test failed ({type(exc).__name__})")
        raise SystemExit(1)
