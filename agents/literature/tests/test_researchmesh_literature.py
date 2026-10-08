import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from semanticscholar.Paper import Paper

from agents.literature.researchmesh_literature import (
    LiteratureAgent,
    LiteratureAgentConfig,
    LiteratureSearchRequest,
    SemanticScholarProvider,
)


class ResearchMeshLiteratureAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_alphaevolve_successful_search(self):
        paper = Paper({
            "paperId": "alphaevolve-paper",
            "title": "AlphaEvolve: A Coding Agent for Scientific Discovery",
            "authors": [{"authorId": "author-1", "name": "A. Researcher"}],
            "year": 2025,
            "abstract": "An evolutionary coding agent.",
            "externalIds": {"DOI": "10.0000/alphaevolve", "ArXiv": "2506.00001"},
            "url": "https://www.semanticscholar.org/paper/alphaevolve-paper",
            "openAccessPdf": {"url": "https://arxiv.org/pdf/2506.00001"},
            "citationCount": 42,
        })
        client = SimpleNamespace(
            search_paper=AsyncMock(
                return_value=SimpleNamespace(items=[paper], total=1)
            )
        )
        provider = SemanticScholarProvider(
            config=LiteratureAgentConfig(rate_limit_per_second=1000),
            client=client,
        )
        agent = LiteratureAgent(provider)
        request = LiteratureSearchRequest(
            requestId="alphaevolve-1", query="AlphaEvolve"
        )

        response = await agent.literature_search(request)
        cached_response = await agent.literature_search(request)

        self.assertEqual(response.status, "success")
        self.assertEqual(response.schemaVersion, "paper_records.v1")
        self.assertEqual(response.papers[0].paperId, "alphaevolve-paper")
        self.assertEqual(response.papers[0].doi, "10.0000/alphaevolve")
        self.assertTrue(cached_response.metrics.cacheHit)
        client.search_paper.assert_awaited_once()

    async def test_rate_limit_returns_failure(self):
        retry_events = []
        client = SimpleNamespace(
            search_paper=AsyncMock(
                side_effect=ConnectionRefusedError("HTTP status 429 Too Many Requests.")
            )
        )
        provider = SemanticScholarProvider(
            config=LiteratureAgentConfig(
                rate_limit_per_second=1000,
                max_retries=1,
                backoff_initial_seconds=0,
                backoff_max_seconds=0,
            ),
            client=client,
            retry_observer=retry_events.append,
        )
        agent = LiteratureAgent(provider)
        request = LiteratureSearchRequest(
            requestId="rate-limit-1", query="AlphaEvolve"
        )

        response = await agent.literature_search(request)

        self.assertEqual(response.status, "failure")
        self.assertEqual(response.error.code, "RATE_LIMITED")
        self.assertTrue(response.error.retryable)
        self.assertEqual(response.metrics.attempts, 2)
        self.assertEqual(response.papers, [])
        self.assertEqual(client.search_paper.await_count, 2)
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(retry_events[0]["reason"], "rate_limit")
        self.assertEqual(retry_events[0]["httpStatus"], 429)

        no_pdf = provider._to_record({
            "paperId": "no-pdf",
            "openAccessPdf": {"url": ""},
        })
        self.assertIsNone(no_pdf.pdfUrl)


if __name__ == "__main__":
    unittest.main()
