from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from .config import RepositoryAgentConfig


class InvalidRepositoryUrl(ValueError):
    pass


class RepositoryCloneError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedRepository:
    path: Path
    origin_url: str
    commit_hash: str
    clone_ms: float


def validate_repository_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise InvalidRepositoryUrl("Repository URL must be a non-empty string")
    value = url.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.path.strip("/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidRepositoryUrl("Only credential-free https Git repository URLs are supported")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


class RepositoryFetcher:
    def __init__(self, config: RepositoryAgentConfig | None = None) -> None:
        self.config = config or RepositoryAgentConfig()

    @contextmanager
    def fetch(self, url: str) -> Iterator[FetchedRepository]:
        safe_url = validate_repository_url(url)
        temp_root = Path(tempfile.mkdtemp(prefix="researchmesh_repository_"))
        repository_path = temp_root / "repository"
        disabled_hooks = temp_root / "disabled-hooks"
        disabled_hooks.mkdir()
        started = time.perf_counter()
        try:
            clone = subprocess.run(
                [
                    "git",
                    "-c",
                    f"core.hooksPath={disabled_hooks}",
                    "clone",
                    "--depth",
                    "1",
                    "--no-tags",
                    safe_url,
                    str(repository_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.clone_timeout_seconds,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            clone_ms = round((time.perf_counter() - started) * 1000, 3)
            if clone.returncode != 0:
                detail = (clone.stderr or clone.stdout or "git clone failed").strip()[-1000:]
                raise RepositoryCloneError(detail.replace(safe_url, "<repository-url>"))
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            if commit.returncode != 0 or not commit.stdout.strip():
                raise RepositoryCloneError("Unable to resolve cloned repository HEAD")
            yield FetchedRepository(
                path=repository_path,
                origin_url=safe_url,
                commit_hash=commit.stdout.strip(),
                clone_ms=clone_ms,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepositoryCloneError(
                f"git clone exceeded {self.config.clone_timeout_seconds:g} seconds"
            ) from exc
        finally:
            if not self.config.debug_keep_temp:
                _remove_temp_root(temp_root)


def _remove_temp_root(temp_root: Path) -> None:
    """Remove a fetch-owned temp tree, including read-only Git pack files on Windows."""

    def make_writable_and_retry(function, path, _error) -> None:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        function(path)

    last_error: OSError | None = None
    for delay in (0.0, 0.1, 0.3):
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(temp_root, onexc=make_writable_and_retry)
        except OSError as exc:
            last_error = exc
        if not temp_root.exists():
            return
    raise RepositoryCloneError(
        f"Unable to clean temporary repository directory: {temp_root.name}"
    ) from last_error
