from __future__ import annotations

import tempfile
import os
import stat
import unittest
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from agents.repository.researchmesh_repository import (
    CodeWikiProvider,
    CodeWikiStaticResult,
    RepositoryAgent,
    RepositoryAnalyzeRequest,
    RepositoryEnricher,
    RepositoryEvidenceCollector,
    StaticAnalysisError,
)
from agents.repository.researchmesh_repository.fetcher import (
    FetchedRepository,
    RepositoryCloneError,
    _remove_temp_root,
)
from agents.repository.researchmesh_repository.schemas import Evidence


def request(**overrides):
    value = {
        "schemaVersion": "0.2",
        "subtaskId": "repo-1",
        "taskType": "repository_analyze",
        "goal": "Analyze the supplied repository",
        "input": {"repositoryUrls": ["https://example.com/tiny.git"]},
        "constraints": {"maxRepositories": 1, "executeCode": False},
        "outputSchema": "code_records.v1",
    }
    value.update(overrides)
    return value


def static_result() -> CodeWikiStaticResult:
    components = {
        "main.py::main": {
            "id": "main.py::main",
            "name": "main",
            "component_type": "function",
            "relative_path": "main.py",
            "depends_on": ["service.py::calculate"],
            "source_code": "def main():\n    return calculate(2, 3)",
            "start_line": 3,
            "end_line": 4,
            "node_type": "function",
        },
        "service.py::calculate": {
            "id": "service.py::calculate",
            "name": "calculate",
            "component_type": "function",
            "relative_path": "service.py",
            "depends_on": ["math_utils.py::add"],
            "source_code": "def calculate(a, b):\n    return add(a, b)",
            "start_line": 3,
            "end_line": 4,
            "node_type": "function",
        },
        "math_utils.py::add": {
            "id": "math_utils.py::add",
            "name": "add",
            "component_type": "function",
            "relative_path": "math_utils.py",
            "depends_on": [],
            "source_code": "def add(a, b):\n    return a + b",
            "start_line": 1,
            "end_line": 2,
            "node_type": "function",
        },
        "pyproject.toml::pyproject.toml": {
            "id": "pyproject.toml::pyproject.toml",
            "name": "pyproject.toml",
            "component_type": "artifact",
            "relative_path": "pyproject.toml",
            "depends_on": ["pyproject.toml::tiny-repo"],
            "source_code": "[project.scripts]\ntiny-repo = \"main:main\"",
            "start_line": 1,
            "end_line": 2,
            "node_type": "artifact_file",
            "artifact_class": "manifest",
        },
        "pyproject.toml::tiny-repo": {
            "id": "pyproject.toml::tiny-repo",
            "name": "tiny-repo",
            "component_type": "artifact",
            "relative_path": "pyproject.toml",
            "depends_on": ["main.py::main"],
            "source_code": "tiny-repo = main:main",
            "start_line": 2,
            "end_line": 2,
            "node_type": "artifact_unit",
            "artifact_class": "manifest",
        },
    }
    index = [
        {"id": key, "type": value["component_type"], "file": value["relative_path"]}
        for key, value in components.items()
    ]
    return CodeWikiStaticResult(
        components=components,
        component_index=index,
        artifact_index={"nodes": 2, "edges": 2},
        static_analysis_ms=12.5,
    )


class FakeStaticProvider:
    def analyze(self, repo_path: Path) -> CodeWikiStaticResult:
        return static_result()


class FailingStaticProvider:
    def analyze(self, repo_path: Path) -> CodeWikiStaticResult:
        raise StaticAnalysisError("parser failed")


class FakeFetcher:
    def __init__(self, path: Path, *, fail: bool = False):
        self.path = path
        self.fail = fail

    @contextmanager
    def fetch(self, url: str):
        if self.fail:
            raise RepositoryCloneError("clone refused")
        yield FetchedRepository(self.path, url, "a" * 40, 4.5)


class FakeLLM:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0

    def generate_json(self, messages, *, schema=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


class RepositoryAgentUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / "README.md").write_text(
            "# Tiny\n\n```bash\npython main.py\n```\n", encoding="utf-8"
        )
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname="tiny"\nversion="0.1"\n'
            '[project.scripts]\ntiny-repo="main:main"\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def agent(self, **kwargs) -> RepositoryAgent:
        return RepositoryAgent(
            fetcher=FakeFetcher(self.repo),
            codewiki_provider=FakeStaticProvider(),
            enrichment_enabled=False,
            **kwargs,
        )

    def test_partner_request_has_exactly_seven_top_level_fields(self):
        RepositoryAnalyzeRequest.model_validate(request())
        self.assertEqual(
            set(RepositoryAnalyzeRequest.model_fields),
            {
                "schemaVersion",
                "subtaskId",
                "taskType",
                "goal",
                "input",
                "constraints",
                "outputSchema",
            },
        )

    def test_required_skill_is_not_accepted_or_required(self):
        RepositoryAnalyzeRequest.model_validate(request())
        with self.assertRaises(ValidationError):
            RepositoryAnalyzeRequest.model_validate(request(requiredSkill="repository.analyze"))

    def test_depends_on_is_not_accepted_or_required(self):
        RepositoryAnalyzeRequest.model_validate(request())
        with self.assertRaises(ValidationError):
            RepositoryAnalyzeRequest.model_validate(request(dependsOn=[]))

    def test_empty_repository_urls_returns_repository_unavailable(self):
        result = self.agent().analyze(request(input={"repositoryUrls": []}))
        self.assertEqual(result.items, [])
        self.assertTrue(result.warnings[0].startswith("REPOSITORY_UNAVAILABLE:"))

    def test_malformed_url_returns_invalid_repository_url(self):
        result = self.agent().analyze(request(input={"repositoryUrls": ["file:///secret"]}))
        self.assertTrue(result.warnings[0].startswith("INVALID_REPOSITORY_URL:"))

    def test_clone_failure_is_explicit(self):
        agent = RepositoryAgent(
            fetcher=FakeFetcher(self.repo, fail=True),
            codewiki_provider=FakeStaticProvider(),
            enrichment_enabled=False,
        )
        result = agent.analyze(request())
        self.assertTrue(result.warnings[0].startswith("REPOSITORY_CLONE_FAILED:"))

    def test_windows_read_only_clone_files_are_cleaned(self):
        root = Path(tempfile.mkdtemp(prefix="repository-cleanup-test-"))
        packed = root / ".git" / "objects" / "pack" / "sample.pack"
        packed.parent.mkdir(parents=True)
        packed.write_bytes(b"pack")
        os.chmod(packed, stat.S_IREAD)
        _remove_temp_root(root)
        self.assertFalse(root.exists())

    def test_static_failure_does_not_fabricate_repository_result(self):
        agent = RepositoryAgent(
            fetcher=FakeFetcher(self.repo),
            codewiki_provider=FailingStaticProvider(),
            enrichment_enabled=False,
        )
        result = agent.analyze(request())
        self.assertEqual(result.items, [])
        self.assertTrue(result.warnings[0].startswith("STATIC_ANALYSIS_FAILED:"))

    def test_multiple_urls_analyzes_first_and_warns(self):
        value = request(
            input={
                "repositoryUrls": [
                    "https://example.com/one.git",
                    "https://example.com/two.git",
                ]
            }
        )
        result = self.agent().analyze(value)
        self.assertEqual(len(result.items), 1)
        self.assertIn("MULTIPLE_REPOSITORIES_IGNORED", result.warnings[0])

    def test_codewiki_components_map_to_structure(self):
        data = self.agent().analyze(request()).items[0].data
        ids = {
            component.componentId
            for file in data.structure
            for component in file.components
        }
        self.assertIn("main.py::main", ids)
        self.assertEqual(data.diagnostics.counts.componentCount, 5)

    def test_dependency_mapping_preserves_chain(self):
        data = self.agent().analyze(request()).items[0].data
        edges = {(edge.source, edge.target) for edge in data.dependencies}
        self.assertIn(("main.py::main", "service.py::calculate"), edges)
        self.assertIn(("service.py::calculate", "math_utils.py::add"), edges)

    def test_component_evidence_preserves_line_range(self):
        data = self.agent().analyze(request()).items[0].data
        item = next(e for e in data.evidence if e.symbol == "main")
        self.assertEqual((item.filePath, item.startLine, item.endLine), ("main.py", 3, 4))

    def test_artifact_entrypoint_mapping(self):
        data = self.agent().analyze(request()).items[0].data
        entry = next(item for item in data.entrypoints if item.name == "tiny-repo")
        self.assertEqual(entry.target, "main:main")
        self.assertTrue(entry.evidenceIds)

    def test_run_commands_are_deterministically_extracted(self):
        data = self.agent().analyze(request()).items[0].data
        commands = {(item.command, item.source) for item in data.runCommands}
        self.assertIn(("tiny-repo", "pyproject.toml"), commands)
        self.assertIn(("python main.py", "README.md"), commands)

    def test_readme_is_collected_as_evidence(self):
        data = self.agent().analyze(request()).items[0].data
        readme = next(e for e in data.evidence if e.filePath == "README.md")
        self.assertEqual(readme.sourceType, "readme")
        self.assertIn("python main.py", readme.text)

    def test_dot_env_is_never_read(self):
        (self.repo / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
        data = self.agent().analyze(request()).items[0].data
        self.assertNotIn(".env", {item.filePath for item in data.evidence})
        self.assertNotIn("do-not-read", "\n".join(item.text for item in data.evidence))

    def test_oversized_and_binary_files_are_skipped(self):
        (self.repo / "requirements-big.txt").write_bytes(b"a" * 2048)
        (self.repo / "requirements-binary.txt").write_bytes(b"a\x00b")
        collector = RepositoryEvidenceCollector(max_file_bytes=1024)
        result = collector.collect(self.repo)
        paths = {item.filePath for item in result.evidence}
        self.assertNotIn("requirements-big.txt", paths)
        self.assertNotIn("requirements-binary.txt", paths)
        self.assertTrue(any("OVERSIZED" in item for item in result.warnings))
        self.assertTrue(any("BINARY" in item for item in result.warnings))

    def test_valid_llm_evidence_ids_are_preserved(self):
        llm = FakeLLM(
            {
                "overview": {"text": "Documented tiny project.", "evidenceIds": ["E1"]},
                "reproductionBarriers": [
                    {"issue": "Requires Python.", "evidenceIds": ["E1"]}
                ],
            }
        )
        result = RepositoryEnricher(llm).enrich(
            [Evidence(evidenceId="E1", sourceType="readme", filePath="README.md", text="Python")],
            entrypoints=[],
            run_commands=[],
            dependency_summary={"edgeCount": 0},
        )
        self.assertEqual(result.overview.evidenceIds, ["E1"])
        self.assertEqual(result.reproduction_barriers[0].evidenceIds, ["E1"])
        self.assertEqual(result.warnings, [])

    def test_hallucinated_llm_evidence_ids_are_removed(self):
        llm = FakeLLM(
            {
                "overview": {"text": "Unsupported.", "evidenceIds": ["E999"]},
                "reproductionBarriers": [
                    {"issue": "Supported.", "evidenceIds": ["E1"]},
                    {"issue": "Invented.", "evidenceIds": ["E777"]},
                ],
            }
        )
        result = RepositoryEnricher(llm).enrich(
            [Evidence(evidenceId="E1", sourceType="readme", filePath="README.md", text="Fact")],
            entrypoints=[],
            run_commands=[],
            dependency_summary={"edgeCount": 0},
        )
        self.assertIsNone(result.overview)
        self.assertEqual(len(result.reproduction_barriers), 1)
        self.assertIn("UNSUPPORTED_LLM_CLAIM", result.warnings)

    def test_empty_llm_response_degrades_gracefully(self):
        result = RepositoryEnricher(FakeLLM(error=ValueError("empty"))).enrich(
            [Evidence(evidenceId="E1", sourceType="readme", filePath="README.md", text="Fact")],
            entrypoints=[],
            run_commands=[],
            dependency_summary={"edgeCount": 0},
        )
        self.assertIsNone(result.overview)
        self.assertEqual(result.reproduction_barriers, [])
        self.assertEqual(result.warnings, ["LLM_ENRICHMENT_UNAVAILABLE"])

    def test_static_success_survives_llm_failure(self):
        enricher = RepositoryEnricher(FakeLLM(error=RuntimeError("offline")))
        agent = RepositoryAgent(
            fetcher=FakeFetcher(self.repo),
            codewiki_provider=FakeStaticProvider(),
            enricher=enricher,
        )
        result = agent.analyze(request())
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].data.diagnostics.counts.componentCount, 5)
        self.assertIn("LLM_ENRICHMENT_UNAVAILABLE", result.warnings)


class TinyRepositoryCodeWikiRegressionTests(unittest.TestCase):
    def test_real_codewiki_static_tiny_repo_chain_and_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text(
                "from service import calculate\n\ndef main():\n    return calculate(2, 3)\n",
                encoding="utf-8",
            )
            (root / "service.py").write_text(
                "from math_utils import add\n\ndef calculate(a, b):\n    return add(a, b)\n",
                encoding="utf-8",
            )
            (root / "math_utils.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
            (root / "pyproject.toml").write_text(
                '[project]\nname="tiny"\nversion="0.1"\n'
                '[project.scripts]\ntiny-repo="main:main"\n',
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "# Tiny\n```bash\npython main.py\n```\n", encoding="utf-8"
            )
            result = RepositoryAgent(
                codewiki_provider=CodeWikiProvider(), enrichment_enabled=False
            ).analyze_local(request(), root)
            self.assertEqual(len(result.items), 1, result.warnings)
            data = result.items[0].data
            edges = {(edge.source, edge.target) for edge in data.dependencies}
            self.assertIn(("main.py::main", "service.py::calculate"), edges)
            self.assertIn(("service.py::calculate", "math_utils.py::add"), edges)
            self.assertTrue(all(e.startLine and e.endLine for e in data.evidence[:5]))
            self.assertTrue(any(e.name == "tiny-repo" for e in data.entrypoints))


if __name__ == "__main__":
    unittest.main()
