# ResearchMesh: Paper Reader v0.2 AIP local integration patch

The patch **does not change** existing `aip/wu_delivery` v0.1 modules, Planner models,
or Literature integration. A separate official-ACPS-SDK Partner supports Reader's
`paper_read` v0.2 input (`papers`, not legacy `paperUrl`), and sends back
`paper_notes.v1` (`paper_note`, not legacy `paper_analysis`).

## Offline checks

From `D:\research\ResearchMesh` in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p "test_aip_reader_v02.py" -v
.\.venv\Scripts\python.exe -X utf8 .\run_reader_aip_v02.py
```

The demo intentionally uses an existing AlphaEvolve literature result with `pdfUrl: null`.
Expected **protocolCompleted=True**, **businessValidated=True**, **researchSucceeded=False**,
and warning `FULL_TEXT_UNAVAILABLE`. No network, LLM, PDF parsing, embedding or key is used.

The 6 offline tests also include a **fake-provider** AIP handshaking fixture. It validates
only the data/protocol shape and **must not be cited as genuine PDF analysis**.
The local runner marks its `backendMode` as `fixture` or `real`; fixture results
can never set `researchSucceeded=True`.

## Later real-PDF validation

1. Install the pinned `agents/paper_reader/requirements.txt` separately (the Git dependency
   and embedding model may be large). Use the version controlled by the team.
2. Create a copy of `examples/reader_aip_no_pdf_v02.json` **inside** `examples/`,
   replacing `pdfUrl` with an independently verified, accessible HTTPS PDF URL.
3. Ensure project-root `.env` contains the Reader's DeepSeek settings, not committed to Git.
4. Explicitly run (may incur external API charges):

```powershell
.\.venv\Scripts\python.exe -X utf8 .\run_reader_aip_v02.py --live --task .\examples\reader_aip_live_v02.json
```

Look for `researchSucceeded=True` and inspect `outputs/reader-aip-v02-*/aip_outcome.json`
for real `fulltext` notes, source and evidence (including URL and nonempty supports).
`protocolCompleted=True` *alone* does not count as research success.

## Scope and limitations

- The demo runs official SDK RPC using an in-memory `httpx.ASGITransport`; no public
  binding, TLS identities, ACS registration, ADP discovery or MQ/AMP support.
- Reader v0.1 handles a maximum of one PDF at a time (`maxPapers: 1`).
- The AIP boundary carries only the seven standard business fields. `requiredSkill`
  and `dependsOn` are Leader-only, and are stripped before sending.
- Leader v0.2's `result_contract.validate_result` is applied to real Reader results;
  a Reader schema-valid result can still be rejected (e.g. missing evidence URL).
- The offline example is one isolated `paper_read` task, **not** the completed
  `literature_search -> paper_read` dependency-binding flow.
- Archive the ZIP under `D:\research\aic\archives`; put source files in the paths
  shown by the ZIP. Do not commit `.env`, `outputs` or saved paper content.
