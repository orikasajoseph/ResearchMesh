# Literature Agent v0.1 Release Notes

## Release Status

Literature Agent v0.1 is **DONE / FROZEN**. Its confirmed contract is:

- skill: `literature.search`
- input: `literature_search.v0.1`
- output: `paper_records.v1`
- provider: Semantic Scholar

The release keeps the upstream Semantic Scholar SDK unchanged and uses the
PyPI dependency `semanticscholar==0.12.0`.

## Real API Acceptance

Latest Semantic Scholar regression:

- query: `AlphaEvolve`
- maxResults: `5`
- returnedCount: `5`
- latencyMs: `2750.866`
- cacheHit: `false`
- retryCount: `0`
- firstPaperHasDoi: `true`
- firstPaperHasPdfUrl: `false`

The missing PDF is expected: `pdfUrl` is populated only from
`openAccessPdf.url` and remains `null` when Semantic Scholar does not provide
that field. A randomized query also completed with zero results and the
normalized warning/failure response; no paper was fabricated.

## Verification

The final Literature Agent unit-test run completed successfully:

```text
Ran 2 tests
OK
```

The shared LLM Provider unit-test run also completed successfully, both public
package imports succeeded, all example JSON files parsed, and both pinned
requirements files passed `pip install --dry-run` in the verification
environment.

DeepSeek LLM Provider v0.1 has been independently verified against the real
API. Literature Agent v0.1 does not depend on the LLM Provider and performs no
LLM call in its retrieval path.

## Known Limitations

- Semantic Scholar may return HTTP 429; retry and exponential backoff are implemented.
- Some papers have no `openAccessPdf`, so `pdfUrl` may be `null`.
- LLM reranking is not implemented.
- OpenAlex fallback is not implemented.
- Real AIP and Planner integration is not yet complete.

All unfinished capabilities above are assigned to v0.2/backlog. They are not
part of the v0.1 release and must not be added during this finalization pass.
