"""Loopback-only official SDK AIP Partner backed by real Literature Agent.

Optional manual server: python -m uvicorn aip.local_literature_app:app --host 127.0.0.1 --port 8011
Identity binding is DISABLED and must never be exposed publicly.
"""
from .literature_bridge import execute_literature
from .wu_delivery.partner import create_local_app

app = create_local_app(execute=execute_literature, backend_timeout_seconds=90.0)
