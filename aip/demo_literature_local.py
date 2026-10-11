"""Run a single real Literature AIP round-trip in memory (SDK required).

From shared repository root: python -m aip.demo_literature_local
No public port, no MQ, no registry identity. Requires local .env API Key.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from .literature_bridge import execute_literature
from .wu_delivery.adapter import invoke_for_planner
from .wu_delivery.contracts import ResearchTask
from .wu_delivery.partner import create_local_app
from .wu_delivery.structured import ResearchClient


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    if not os.getenv('SEMANTIC_SCHOLAR_API_KEY'):
        print('SEMANTIC_SCHOLAR_API_KEY is not configured in root .env; no API call made.')
        return 2
    payload = {
        'schemaVersion': '0.1', 'subtaskId': 'S1',
        'taskType': 'literature_search', 'goal': '检索 AlphaEvolve 论文',
        'input': {'query': 'AlphaEvolve'}, 'constraints': {'maxResults': 5},
        'outputSchema': 'paper_records.v1',
    }
    request = ResearchTask.model_validate(payload)
    server = create_local_app(execute=execute_literature, backend_timeout_seconds=90.0)
    client = ResearchClient(
        partner_url='http://127.0.0.1:8011/rpc',
        leader_id='local-example-leader', identity_binding_enabled=False,
        transport=httpx.ASGITransport(app=server),
    )
    try:
        outcome = await invoke_for_planner(
            client, request, session_id=str(uuid4()), task_id=str(uuid4()),
            timeout_seconds=100.0,
        )
        data = outcome.to_dict()
        # Don't print exception details or secrets from upstream.
        if data.get('error'):
            data['error'] = data['error'].split(':', 1)[0]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if outcome.protocol_completed and outcome.business_ready and not outcome.error else 1
    finally:
        await client.close()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
