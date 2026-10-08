import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from common.llm.researchmesh_llm import LLMConfig, LLMProvider


def _response(content: str):
    return SimpleNamespace(
        id="response-test-id",
        model="deepseek-test",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


class ResearchMeshLLMProviderTests(unittest.TestCase):
    def _provider(self, side_effect) -> tuple[LLMProvider, Mock]:
        create = Mock(side_effect=side_effect)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        config = LLMConfig(
            api_key="not-a-real-key",
            base_url="https://example.invalid",
            model="deepseek-test",
            timeout_seconds=1,
            max_retries=0,
        )
        return LLMProvider(config=config, client=client), create

    def test_chat_returns_normalized_response(self):
        provider, create = self._provider([_response("A concise answer.")])

        response = provider.chat([
            {"role": "user", "content": "Explain reinforcement learning."}
        ])

        self.assertEqual(response.provider, "deepseek")
        self.assertEqual(response.content, "A concise answer.")
        self.assertEqual(response.usage.totalTokens, 15)
        self.assertEqual(response.requestId, "response-test-id")
        create.assert_called_once()

    def test_generate_json_retries_one_parse_failure(self):
        provider, create = self._provider([
            _response("not-json"),
            _response('{"topic":"AlphaEvolve"}'),
        ])

        result = provider.generate_json([
            {"role": "user", "content": "Return a research topic as JSON."}
        ])

        self.assertEqual(result, {"topic": "AlphaEvolve"})
        self.assertEqual(create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
