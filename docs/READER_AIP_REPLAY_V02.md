# Archived Paper Reader full-text result → AIP v0.2 replay

This is a **transport and contract replay**, not a new PaperQA execution. The
input is the previously saved `agents/paper_reader/output/paper_reader_live.json`
that reported `status=PASS`. Existing AIP SDK, bridges, Literature agent and
PaperQA code are unchanged.

From the ResearchMesh root, in the existing **main** `.venv` (no `paper-qa` is
required in this environment):

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_aip_reader_replay_v02.py' -v
.\.venv\Scripts\python.exe -X utf8 .\run_reader_aip_replay_v02.py
```

The replay reads the existing real Reader `warmResult`, constructs a matching
v0.2 `paper_read` business task using the **original** PaperRef and subtaskId,
checks it against the Leader v0.2 `validate_result`, then sends it through the
official SDK and local ASGI AIP Partner. The Partner backend returns the exact
archived PaperReadResult without re-running PaperQA or DeepSeek.

Acceptance criteria:

- `AIP status: completed`
- `protocolCompleted: True`
- `businessValidated: True`
- `aipReplayAccepted: True`
- `researchSucceeded: False` — intentional: it was **not newly executed**
- archived `analysisLevel=fulltext` and evidence count are preserved

Evidence is written to `ResearchMesh/outputs/reader-aip-replay-<UTC-timestamp>`:
`business_task_v02.json`, `aip_outcome.json`, `replay_assessment.json`.
The assessment includes the source archive SHA-256.

**Important:** The six `http://127.0.0.1:.../paper.pdf` references from the
previous live test are historical localhost URLs. Their syntax can pass Leader
validation, but they are not durable network-accessible evidence for a remote
Leader. Do not change them to invented public URLs. Platform ACS/ADP/MQ and
live-Reader-inside-AIP execution remain separate future acceptance steps.
