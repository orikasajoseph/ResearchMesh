import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from agents.paper_reader.researchmesh_paper_reader import (
    FULL_TEXT_UNAVAILABLE,
    PAPERQA_CITATION_FORMATTING_INCOMPLETE,
    PAPERQA_STRUCTURED_OUTPUT_INCOMPLETE,
    FullTextUnavailableError,
    PaperQAProvider,
    PaperReaderConfig,
    PaperReaderAgent,
    PaperReadTask,
    PaperRef,
    ProviderAnalysis,
    ProviderContext,
)


def valid_paper(**overrides):
    data = {
        "paperId": "paper-1",
        "title": "A Real Paper",
        "doi": "10.1234/example.1",
        "url": "https://example.org/paper/1",
        "pdfUrl": "https://example.org/paper/1.pdf",
        "repositoryUrls": [],
    }
    data.update(overrides)
    return data


def valid_task(**overrides):
    data = {
        "schemaVersion": "0.2",
        "subtaskId": "paper-read-1",
        "taskType": "paper_read",
        "goal": "Analyze one paper",
        "input": {"papers": [valid_paper()]},
        "constraints": {"maxPapers": 1},
        "outputSchema": "paper_notes.v1",
    }
    data.update(overrides)
    return data


def provider_analysis(*, citation_incomplete=False, structured=True):
    answer = (
        "RESEARCH_PROBLEM: Explain black-box molecular models.\n"
        "METHOD: A two-step XAI method (pqac-1234abcd).\n"
        "FINDINGS: Explanations reveal structure-property relationships "
        "(pqac-1234abcd).\n"
        "LIMITATIONS: Evaluation remains subjective (pqac-1234abcd)."
        if structured
        else "An unstructured answer."
    )
    return ProviderAnalysis(
        answer=answer,
        contexts=[
            ProviderContext(
                context_id="pqac-1234abcd",
                locator="PaperOne pages 3-5",
                raw_text="The method reveals relationships but evaluation is subjective.",
                summary="Method, findings, and limitations are described.",
                score=9,
                source="A Real Paper",
            )
        ],
        references="1. PaperOne pages 3-5: A Real Paper",
        timings={"totalMs": 12.0},
        diagnostics={
            "rawAnswer": answer,
            "citationFormattingIncomplete": citation_incomplete,
        },
    )


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result or provider_analysis()
        self.error = error
        self.calls = []

    async def analyze(self, paper):
        self.calls.append(paper)
        if self.error is not None:
            raise self.error
        return self.result


class PaperReaderSchemaTests(unittest.TestCase):
    def test_v02_business_payload_accepts_only_partner_fields(self):
        task = PaperReadTask.model_validate(valid_task())

        self.assertEqual(task.schemaVersion, "0.2")
        self.assertEqual(task.taskType, "paper_read")
        self.assertEqual(task.outputSchema, "paper_notes.v1")

        for forbidden in ("requiredSkill", "dependsOn"):
            payload = valid_task(**{forbidden: [] if forbidden == "dependsOn" else "paper.analyze"})
            with self.assertRaises(ValidationError):
                PaperReadTask.model_validate(payload)

    def test_paper_ref_is_exactly_six_fields(self):
        paper = PaperRef.model_validate(
            valid_paper(title=None, url=None, pdfUrl=None, repositoryUrls=[])
        )

        self.assertEqual(
            set(paper.model_dump()),
            {"paperId", "title", "doi", "url", "pdfUrl", "repositoryUrls"},
        )
        self.assertIsNone(paper.title)
        self.assertEqual(paper.repositoryUrls, [])

        with self.assertRaises(ValidationError):
            PaperRef.model_validate({**valid_paper(), "artifactId": "forbidden"})

    def test_paper_ref_rejects_title_only(self):
        with self.assertRaises(ValidationError):
            PaperRef.model_validate(
                {
                    "paperId": "paper-1",
                    "title": "Title only",
                    "doi": None,
                    "url": None,
                    "pdfUrl": None,
                    "repositoryUrls": [],
                }
            )

    def test_max_papers_is_one(self):
        payload = valid_task()
        payload["input"]["papers"].append(valid_paper(paperId="paper-2"))

        with self.assertRaises(ValidationError):
            PaperReadTask.model_validate(payload)

        payload = valid_task(constraints={"maxPapers": 2})
        with self.assertRaises(ValidationError):
            PaperReadTask.model_validate(payload)

    def test_output_schema_is_frozen(self):
        with self.assertRaises(ValidationError):
            PaperReadTask.model_validate(valid_task(outputSchema="paper_analysis.v1"))


class PaperReaderAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_pdf_url_returns_full_text_unavailable(self):
        provider = FakeProvider()
        agent = PaperReaderAgent(provider)
        payload = valid_task()
        payload["input"]["papers"][0]["pdfUrl"] = None

        result = await agent.run(payload)

        self.assertEqual(result.items, [])
        self.assertEqual(result.warnings, [FULL_TEXT_UNAVAILABLE])
        self.assertEqual(provider.calls, [])
        self.assertNotIn("status", result.model_dump())

    async def test_provider_maps_to_fulltext_paper_note(self):
        provider = FakeProvider()
        result = await PaperReaderAgent(provider).run(valid_task())

        self.assertEqual(result.schemaVersion, "0.2")
        self.assertEqual(result.taskType, "paper_read")
        self.assertEqual(result.items[0].kind, "paper_note")
        note = result.items[0].data
        self.assertEqual(note.analysisLevel, "fulltext")
        self.assertEqual(note.source.model_dump(), valid_paper())
        self.assertIn("two-step", note.method)
        self.assertIn("structure-property", note.findings)
        self.assertIn("subjective", note.limitations)

    async def test_evidence_uses_context_as_primary_source(self):
        result = await PaperReaderAgent(FakeProvider()).run(valid_task())
        evidence = result.items[0].data.evidence[0]

        self.assertEqual(evidence.locator, "PaperOne pages 3-5")
        self.assertEqual(evidence.text, "The method reveals relationships but evaluation is subjective.")
        self.assertEqual(evidence.summary, "Method, findings, and limitations are described.")
        self.assertEqual(evidence.score, 9)
        self.assertIsInstance(evidence.supports, str)
        self.assertIn("two-step XAI method", evidence.supports)
        self.assertIn("structure-property relationships", evidence.supports)
        self.assertIn("Evaluation remains subjective", evidence.supports)

    async def test_pqac_failure_warns_but_keeps_context_evidence(self):
        provider = FakeProvider(provider_analysis(citation_incomplete=True))

        result = await PaperReaderAgent(provider).run(valid_task())

        self.assertIn(PAPERQA_CITATION_FORMATTING_INCOMPLETE, result.warnings)
        self.assertEqual(len(result.items[0].data.evidence), 1)
        serialized = result.model_dump_json()
        self.assertNotIn("pqac-", serialized)

    async def test_unstructured_answer_returns_incomplete_without_fake_note(self):
        provider = FakeProvider(provider_analysis(structured=False))

        result = await PaperReaderAgent(provider).run(valid_task())

        self.assertEqual(result.items, [])
        self.assertIn(PAPERQA_STRUCTURED_OUTPUT_INCOMPLETE, result.warnings)

    async def test_provider_fulltext_failure_returns_warning(self):
        provider = FakeProvider(error=FullTextUnavailableError("not a PDF"))

        result = await PaperReaderAgent(provider).run(valid_task())

        self.assertEqual(result.items, [])
        self.assertEqual(result.warnings, [FULL_TEXT_UNAVAILABLE])

    async def test_skill_is_business_skill_only(self):
        agent = PaperReaderAgent(FakeProvider())
        result = await agent.run(valid_task())

        self.assertEqual(agent.skill, "paper.analyze")
        self.assertNotIn("requiredSkill", result.model_dump_json())
        self.assertNotIn("dependsOn", result.model_dump_json())


class PaperQAProviderUtilityTests(unittest.TestCase):
    config = PaperReaderConfig(api_key="test-key", api_base="https://example.invalid",
                               model="synthetic-model")

    @staticmethod
    def fake_settings(*, settings_error=None, embedding_error=None):
        class Model:
            def __init__(self):
                self.llm_config = SimpleNamespace(
                    models=[SimpleNamespace(extra_params={})]
                )
            async def call_single(self, **kwargs):
                return SimpleNamespace(text=(
                    "RESEARCH_PROBLEM: A problem [evidence-1].\n"
                    "METHOD: A method [evidence-1].\n"
                    "FINDINGS: A finding [evidence-1].\n"
                    "LIMITATIONS: A limitation [evidence-1]."
                ))
        def model():
            return Model()
        class Settings:
            def __init__(self, **kwargs):
                if settings_error:
                    raise settings_error
                self.parsing = SimpleNamespace(parse_pdf=lambda value: value)
                self.answer = SimpleNamespace()
                self.kwargs = kwargs
            def get_embedding_model(self):
                if embedding_error:
                    raise embedding_error
                return "embedding"
            def get_llm(self):
                return model()
            def get_summary_llm(self):
                return model()
        return Settings

    def construct_with(self, *, module):
        events = []
        with patch.dict(sys.modules, {"paperqa": module}):
            provider = PaperQAProvider(self.config, initialization_audit=lambda *event: events.append(event))
        return provider, events

    def test_initialization_stage_order_matches_real_constructor(self):
        provider, events = self.construct_with(module=SimpleNamespace(
            Docs=object, Settings=self.fake_settings()))
        self.assertEqual(provider.embedding_model, "embedding")
        self.assertEqual(events, [
            ("paperqa_dependencies_import", "started"),
            ("paperqa_dependencies_import", "completed"),
            ("paperqa_settings_construct", "started"),
            ("paperqa_settings_construct", "completed"),
            ("embedding_model_load", "started"),
            ("embedding_model_load", "completed")])

    def test_each_initialization_failure_is_audited_and_propagated(self):
        cases = [
            ("paperqa_dependencies_import", None, ModuleNotFoundError),
            ("paperqa_settings_construct", SimpleNamespace(
                Docs=object, Settings=self.fake_settings(settings_error=ValueError("private setting"))), ValueError),
            ("embedding_model_load", SimpleNamespace(
                Docs=object, Settings=self.fake_settings(embedding_error=RuntimeError("private embedding"))), RuntimeError),
        ]
        for stage, module, error_type in cases:
            with self.subTest(stage=stage):
                events = []
                modules = {"paperqa": module}
                with patch.dict(sys.modules, modules):
                    with self.assertRaises(error_type):
                        PaperQAProvider(self.config, initialization_audit=lambda *event: events.append(event))
                self.assertEqual(events[-1], (stage, "failed", error_type.__name__))
                self.assertNotIn("private", str(events))

    def test_frozen_paperqa_settings_defaults(self):
        config = PaperReaderConfig(
            api_key="test-key",
            api_base="https://api.deepseek.com",
            model="deepseek-flash",
        )

        self.assertEqual(config.embedding, "st-multi-qa-MiniLM-L6-cos-v1")
        self.assertEqual(config.evidence_k, 6)
        self.assertEqual(config.max_concurrent_requests, 4)
        self.assertEqual(config.provider_model, "deepseek/deepseek-flash")

    def test_generation_and_summary_router_disable_all_automatic_retries(self):
        config = PaperReaderConfig(
            api_key="test-key",
            api_base="https://api.deepseek.com",
            model="deepseek-flash",
        )

        router = PaperQAProvider.router_config(config)

        self.assertEqual(router["router_kwargs"], {"num_retries": 0})
        self.assertEqual(
            router["model_list"][0]["litellm_params"]["max_retries"], 0
        )

    def test_http_client_is_runtime_only_on_single_answer_model(self):
        module = SimpleNamespace(Docs=object, Settings=self.fake_settings())
        client = object()
        with patch.dict(sys.modules, {"paperqa": module}), \
             patch("model_http_audit.AuditedLiteLLMHTTPHandler", return_value=client):
            provider = PaperQAProvider(self.config, model_http_audit=object())

        for key in ("llm_config", "summary_llm_config"):
            params = provider.settings.kwargs[key]["model_list"][0]["litellm_params"]
            self.assertNotIn("client", params)
        self.assertIs(
            provider._llm_model.llm_config.models[0].extra_params["client"], client
        )

    def test_single_call_path_retrieves_evidence_without_aquery(self):
        captured = {}
        class Docs:
            async def aadd(self, *args, **kwargs):
                return "doc"
            async def retrieve_texts(self, *args, **kwargs):
                captured["retrieve"] = kwargs
                return [SimpleNamespace(
                    name="PaperOne pages 3-5",
                    text="The method works, with a stated limitation.",
                    doc=SimpleNamespace(formatted_citation="A Real Paper"),
                )]
            async def aquery(self, *args, **kwargs):
                raise AssertionError("Docs.aquery must not be used")
        module = SimpleNamespace(Docs=Docs, Settings=self.fake_settings())
        client = object()
        async def fetch(_):
            return b"%PDF-offline"
        with patch.dict(sys.modules, {"paperqa": module}), \
             patch("model_http_audit.AuditedLiteLLMHTTPHandler", return_value=client):
            provider = PaperQAProvider(self.config, pdf_fetcher=fetch,
                                       model_http_audit=object())
        import asyncio
        agent = PaperReaderAgent(provider)
        result = asyncio.run(agent.run(valid_task()))
        analysis = agent.last_provider_analysis
        self.assertIsNotNone(analysis)
        self.assertIn("RESEARCH_PROBLEM", analysis.answer)
        self.assertEqual(analysis.contexts[0].locator, "PaperOne pages 3-5")
        self.assertEqual(analysis.diagnostics["modelCallPlan"], "single_answer_call")
        self.assertEqual(analysis.diagnostics["maximumPhysicalModelRequests"], 1)
        self.assertIn("embedding_model", captured["retrieve"])
        self.assertEqual(result.items[0].kind, "paper_note")
        self.assertEqual(result.items[0].data.analysisLevel, "fulltext")
        self.assertTrue(result.items[0].data.evidence)

    def test_pqac_ids_are_removed_from_display_answer(self):
        answer = "METHOD: supported (pqac-deadbeef). FINDINGS: pqac-1234abcd."

        cleaned = PaperQAProvider.sanitize_answer(answer)

        self.assertNotIn("pqac-", cleaned)
        self.assertIn("METHOD", cleaned)


if __name__ == "__main__":
    unittest.main()
