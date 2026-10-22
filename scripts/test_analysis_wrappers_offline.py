"""Run native wrapper tests in its own runtime; block all external sockets."""
import argparse
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def blocked(*args, **kwargs):
    raise AssertionError("OFFLINE_WRAPPER_NETWORK_FORBIDDEN")


original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def local_only(original):
    def connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"} or address[1] < 49152:
            return blocked()
        return original(sock, address)
    return connect


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=("reader", "repository"))
    args = parser.parse_args()
    os.environ.update(PYTHON_DOTENV_DISABLED="1", LITELLM_LOCAL_MODEL_COST_MAP="True",
        TIKTOKEN_CACHE_DIR=str(Path(sys.prefix) / "runtime-cache/tiktoken"), HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    name = ("agents.paper_reader.tests.test_researchmesh_paper_reader" if args.agent == "reader"
            else "agents.repository.tests.test_repository_agent")
    with patch("socket.socket.connect", local_only(original_connect)), \
         patch("socket.socket.connect_ex", local_only(original_connect_ex)), \
         patch("socket.getaddrinfo", blocked):
        suite = unittest.defaultTestLoader.loadTestsFromName(name)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
