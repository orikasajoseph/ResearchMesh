# Repository Agent AIP v0.2: local integration (2026-10-12)

The bridge is independently added to Wu's integration branch; it **does not edit** Zhang's `RepositoryAgent` or the frozen Wu v0.1 delivery.

## Copy into the existing ResearchMesh repository (PowerShell)

First extract the archive into `D:\research\aic\repository_aip_v02_package`, then copy the file tree. **Do not blindly overwrite pre-existing files**; check `git status --short`.

```powershell
cd D:\research\ResearchMesh
$src = 'D:\research\aic\repository_aip_v02_package'
Copy-Item "$src\aip\repository_v02_bridge.py" .\aip\
Copy-Item "$src\run_repository_aip_v02.py" .\
Copy-Item "$src\tests\test_aip_repository_v02.py" .\tests\
Copy-Item "$src\examples\repository_aip_fixture_v02.json" .\examples\
Copy-Item "$src\docs\REPOSITORY_AIP_V02_README.md" .\docs\
```

## Prerequisites and tests (no CodeWiki or API required)

Requires the EXISTING `.venv` with ACPS SDK 2.2.0, httpx, FastAPI, Pydantic and Zhang's `agents/repository` source already present.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p "test_aip_repository_v02.py" -v
.\.venv\Scripts\python.exe -X utf8 run_repository_aip_v02.py
```

Expect `protocolCompleted: True`, `businessValidated: True`, `researchSucceeded: False`, and `mode: OFFLINE_FIXTURE_NO_REAL_RESEARCH` for the fixture. The fixture still runs Zhang's RepositoryAgent through injected FakeFetcher and FakeStaticProvider, NOT the real CodeWiki backend.

Outputs saved to `outputs/repository-aip-v02-*`; do not commit outputs or secrets.

## Real backend (separate Python 3.12 / CodeWiki environment ONLY)

The explicit `--real --allow-git-clone` flags enable a real Git clone and static analysis without paid LLM enrichment. Do not use these flags until CodeWiki is installed in an isolated, matching Python 3.12 environment.

```powershell
.\.venv-repository\Scripts\python.exe -X utf8 run_repository_aip_v02.py --real --allow-git-clone
```

This is **not a claim of full research reproduction**: it analyzes static repository code only. Constructed GitHub SHA permalinks are derived from Git clone metadata and require link checking before publication. Other repository hosts currently fail conservative evidence normalization rather than inventing URLs.

## Scope and limitations

- Local AIP `start -> awaiting-completion -> complete`; no ACS/ADP/MQ, platform registration, remote identity binding or remote URL verification.
- Planner `requiredSkill/dependsOn` are internal; the wire task has exactly seven business fields. A non-empty `dependsOn` must be resolved by Leader **before** it reaches this runner.
- `allowRepositorySearch=false` is checked, then stripped because Zhang's StrictModel does not define it. `true` is rejected.
- Zhang returns `kind=repository_record`; Zhao requires `kind=repository`. Unknown license is `UNKNOWN` and paper relation is explicitly unverified.
- Real evidence requires a GitHub HTTPS owner/repo URL, a 40-hex SHA from a real clone, and safe file paths; otherwise the Partner fails validation.
- Async timeout does not forcibly stop an already running thread. Zhang's fetcher must enforce its own clone timeout.
- `protocolCompleted` means SDK lifecycle succeeded, `businessValidated` means Zhao's validator accepted the shape, `researchSucceeded` is false for fixture, incomplete/empty results, or failed business conditions.

## Before merging

1. Verify `common.models.ResearchResult` accepts the extra Repository data fields.
2. Have Zhao confirm `repository` item shape and `input_binding.web_url` rules.
3. Have Zhang confirm that repository evidence supports and SHA mapping are accurate for his provider.
4. Run the real CodeWiki integration test in the isolated venv before claiming live analysis.
