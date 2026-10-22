"""Run Literature regressions with external sockets and real MQ disabled."""
import asyncio
import argparse
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--pattern", action="append")
args = parser.parse_args()
ROOT = args.root.resolve()
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def blocked(*args, **kwargs):
    raise AssertionError("OFFLINE_TEST_NETWORK_FORBIDDEN")


original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def local_only(original):
    def connect(sock, address):
        # Windows asyncio needs an ephemeral loopback socketpair for its event loop.
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"} or address[1] < 49152:
            return blocked()
        return original(sock, address)
    return connect


if __name__ == "__main__":
    patterns = args.pattern or ["test_literature_acceptance_audit.py", "test_group*.py", "test_semantic_http.py",
                "test_literature_adapter.py", "test_literature_resources.py",
                "test_aip_literature_bridge.py", "test_wu_integration.py", "test_research_pipeline.py"]
    with (patch("socket.socket.connect", local_only(original_connect)),
          patch("socket.socket.connect_ex", local_only(original_connect_ex)),
          patch("socket.getaddrinfo", blocked),
          patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=blocked),
          patch("httpx.HTTPTransport.handle_request", side_effect=blocked),
          patch("aio_pika.connect", side_effect=blocked),
          patch("aio_pika.connect_robust", side_effect=blocked)):
        suite = unittest.TestSuite()
        for pattern in patterns:
            suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
