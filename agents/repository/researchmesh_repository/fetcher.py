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
    def __init__(self, message: str, *, diagnostics: list[dict] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = list(diagnostics or [])


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
        self.git_diagnostics: list[dict] = []

    @contextmanager
    def fetch(self, url: str) -> Iterator[FetchedRepository]:
        safe_url = validate_repository_url(url)
        self.git_diagnostics = []
        temp_root = Path(tempfile.mkdtemp(prefix="researchmesh_repository_"))
        repository_path = temp_root / "repository"
        disabled_hooks = temp_root / "disabled-hooks"
        disabled_hooks.mkdir()
        clone_error: RepositoryCloneError | None = None
        try:
            started = time.perf_counter()
            try:
                clone = subprocess.run(
                    _git_command(
                        "-c", f"core.hooksPath={disabled_hooks}", "clone",
                        "--depth", "1", "--no-tags", safe_url, str(repository_path),
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.config.clone_timeout_seconds,
                    check=False,
                    env=_git_environment(),
                )
            except subprocess.TimeoutExpired as exc:
                clone_ms = _elapsed_ms(started)
                diagnostic = _diagnostic(
                    "clone", safe_url, None, True, _timeout_stderr(exc), clone_ms, None,
                )
                self.git_diagnostics.append(diagnostic)
                raise RepositoryCloneError(
                    f"git clone exceeded {self.config.clone_timeout_seconds:g} seconds",
                    diagnostics=self.git_diagnostics,
                ) from exc
            except (OSError, ValueError) as exc:
                clone_ms = _elapsed_ms(started)
                diagnostic = _diagnostic(
                    "clone", safe_url, None, False, str(exc), clone_ms, None,
                )
                self.git_diagnostics.append(diagnostic)
                raise RepositoryCloneError(
                    "Unable to start git clone", diagnostics=self.git_diagnostics,
                ) from exc
            clone_ms = _elapsed_ms(started)
            self.git_diagnostics.append(_diagnostic(
                "clone", safe_url, clone.returncode, False,
                clone.stderr or clone.stdout, clone_ms, None,
            ))
            if clone.returncode != 0:
                detail = _sanitize_stderr(clone.stderr or clone.stdout, safe_url)
                raise RepositoryCloneError(
                    detail or "git clone failed", diagnostics=self.git_diagnostics,
                )
            started = time.perf_counter()
            try:
                commit = subprocess.run(
                    _git_command("rev-parse", "HEAD"),
                    cwd=repository_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    check=False,
                    env=_git_environment(),
                )
            except subprocess.TimeoutExpired as exc:
                duration = _elapsed_ms(started)
                self.git_diagnostics.append(_diagnostic(
                    "head", safe_url, None, True, _timeout_stderr(exc), duration, None,
                ))
                raise RepositoryCloneError(
                    "git rev-parse exceeded 15 seconds", diagnostics=self.git_diagnostics,
                ) from exc
            except (OSError, ValueError) as exc:
                duration = _elapsed_ms(started)
                self.git_diagnostics.append(_diagnostic(
                    "head", safe_url, None, False, str(exc), duration, None,
                ))
                raise RepositoryCloneError(
                    "Unable to start git rev-parse", diagnostics=self.git_diagnostics,
                ) from exc
            resolved = commit.stdout.strip() if commit.returncode == 0 else None
            self.git_diagnostics.append(_diagnostic(
                "head", safe_url, commit.returncode, False,
                commit.stderr or ("" if resolved else commit.stdout),
                _elapsed_ms(started), resolved,
            ))
            if commit.returncode != 0 or not commit.stdout.strip():
                raise RepositoryCloneError(
                    "Unable to resolve cloned repository HEAD",
                    diagnostics=self.git_diagnostics,
                )
            yield FetchedRepository(
                path=repository_path,
                origin_url=safe_url,
                commit_hash=commit.stdout.strip(),
                clone_ms=clone_ms,
            )
        except RepositoryCloneError as exc:
            clone_error = exc
            raise
        finally:
            if not self.config.debug_keep_temp:
                started = time.perf_counter()
                try:
                    _remove_temp_root(temp_root)
                except OSError as exc:
                    self.git_diagnostics.append(_diagnostic(
                        "cleanup", safe_url, None, False, str(exc),
                        _elapsed_ms(started), None,
                    ))
                    if clone_error is None:
                        raise RepositoryCloneError(
                            "Unable to clean temporary repository directory",
                            diagnostics=self.git_diagnostics,
                        ) from exc
                else:
                    self.git_diagnostics.append(_diagnostic(
                        "cleanup", safe_url, None, False, "",
                        _elapsed_ms(started), None,
                    ))


def _git_command(*arguments: str) -> list[str]:
    # Command-level empty proxy values override user/global Git config.  The
    # setting applies only to this child process and never writes configuration.
    return ["git", "-c", "http.proxy=", "-c", "https.proxy=", *arguments]


def _git_environment() -> dict[str, str]:
    # Git/libcurl also consult conventional proxy variables.  Remove only those
    # variables from this child environment; retain every unrelated variable.
    proxy_names = {
        "http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    }
    environment = {key: value for key, value in os.environ.items() if key not in proxy_names}
    environment.update(GIT_TERMINAL_PROMPT="0", NO_PROXY="github.com", no_proxy="github.com")
    return environment


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _timeout_stderr(error: subprocess.TimeoutExpired) -> str:
    value = error.stderr or error.stdout or ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value)


def _sanitize_stderr(value: object, repository_url: str) -> str:
    import re

    text = str(value or "").replace(repository_url, "<repository-url>")
    text = re.sub(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@", r"\1<redacted>@", text)
    text = re.sub(
        r"(?i)\b(access[_-]?token|auth[_-]?token|password|secret|api[_-]?key)\s*[=:]\s*[^\s]+",
        r"\1=<redacted>", text,
    )
    return text.strip()[-1000:]


def _diagnostic(stage: str, repository_url: str, returncode: int | None,
                timed_out: bool, stderr: object, duration_ms: float,
                resolved_commit: str | None) -> dict:
    return {
        "stage": stage,
        "returncode": returncode,
        "timedOut": timed_out,
        "stderr": _sanitize_stderr(stderr, repository_url),
        "durationMs": duration_ms,
        "repositoryUrl": repository_url,
        "resolvedCommit": resolved_commit,
    }


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
    raise OSError(f"Unable to clean temporary repository directory: {temp_root.name}") from last_error
