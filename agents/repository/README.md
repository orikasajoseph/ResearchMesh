# Repository Agent v0.1

Skill: `repository.analyze`

Repository Agent accepts one explicit HTTPS Git repository URL, performs a safe
shallow clone, runs CodeWiki's static `handle_analyze_repo()` path, normalizes
components and dependency edges deterministically, collects bounded evidence,
and optionally performs one evidence-grounded enrichment through ResearchMesh's
shared DeepSeek provider.

## Contract status

`origin/main` currently contains Task v0.1. The target Task v0.2 names used here
are therefore local and provisional:

- `schemaVersion`: `0.2`
- `taskType`: `repository_analyze`
- `outputSchema`: `code_records.v1`
- result item `kind`: `repository_record`

The Partner request has exactly seven top-level business fields:
`schemaVersion`, `subtaskId`, `taskType`, `goal`, `input`, `constraints`, and
`outputSchema`. Leader-only `requiredSkill`/`dependsOn` are neither required nor
accepted. Shared schemas are intentionally unchanged pending team confirmation.

For S2→S3, `input.paperNotes` is optional. The local AIP Partner validates each
`paper_notes.v1` note, then forwards at most three notes and six bounded evidence
contexts per note. Its generated `P-…` IDs remain stable for the same source
evidence. Repository enrichment may return `paperCodeRelations`; every such
claim must cite both a forwarded paper evidence ID and a CodeWiki evidence ID.
The result retains these references in `paperEvidence` and `evidence`. Unknown
IDs and claims of executed code or completed reproduction are rejected. When
notes are absent, the original repository-only analysis still runs.

## Capabilities

- Analyze one explicitly supplied repository URL.
- Clone with `--depth 1 --no-tags`, disabled hooks, no submodules, and a timeout.
- Run CodeWiki static analysis without CodeWiki LLM/document generation paths.
- Return file/component/symbol/start/end-line evidence.
- Return internal dependency/call edges from CodeWiki `depends_on` data.
- Extract declared entrypoints and run commands from supported manifests,
  Dockerfiles, Makefiles, CI workflows, and README fenced command blocks.
- Collect bounded evidence from a strict file whitelist; `.env`, `.git`, data,
  checkpoints, large files, binaries, and symlinks are not read.
- Ask the shared ResearchMesh LLM provider only for `overview` and
  `reproductionBarriers`; unknown evidence IDs invalidate the claim.
- Preserve static results when LLM enrichment is unavailable.

## Explicit non-capabilities

The v0.1 agent does **not** search GitHub, infer a repository from a paper or
DOI, choose among repository candidates, execute repository code, install the
target project's dependencies, run npm/pip/build/test/benchmark commands,
verify reproduction, repair code, run SWE-agent, or run CodeWiki `generate`,
module agents, CAW, Web UI, or CodeWiki-owned LLM providers. `executeCode` is
always `false`.

## Installation

Python 3.12 is required. CodeWiki is pinned to the Windows-smoke-tested commit
`f0975ea19b17568ceb55cd5422206e1a1ae13c0b` (`2.0.0`). CodeWiki installation
also requires Node/npm. On Windows PowerShell use `npm.cmd --version` if the
`npm.ps1` shim is blocked by Execution Policy; do not change system policy.

```powershell
python -m pip install -r agents/repository/requirements.txt
```

## Use

```python
from agents.repository.researchmesh_repository import RepositoryAgent

result = RepositoryAgent().analyze({
    "schemaVersion": "0.2",
    "subtaskId": "repo-1",
    "taskType": "repository_analyze",
    "goal": "Describe the supplied repository",
    "input": {"repositoryUrls": ["https://github.com/pypa/sampleproject.git"]},
    "constraints": {"maxRepositories": 1, "executeCode": False},
    "outputSchema": "code_records.v1"
})
```

The development-only `analyze_local()` method supports local fixture regression;
local absolute paths are not part of the Partner business schema.

## Test

```powershell
python -m unittest agents.repository.tests.test_repository_agent -v
python agents/repository/scripts/live_test_repository.py `
  --tiny-repo <TINY_REPO_DIR> `
  --output agents/repository/output/live_repository.json
```

The live script never executes target-repository commands. It performs one local
tiny-repository analysis and one small public-repository shallow-clone analysis.
