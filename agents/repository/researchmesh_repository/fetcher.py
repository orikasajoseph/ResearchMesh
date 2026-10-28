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
import re

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


@dataclass(frozen=True)
class GitStageResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: float


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
        primary_error: RepositoryCloneError | None = None
        try:
            clone = _run_git_stage(
                _git_command(
                    "-c", "protocol.version=2",
                    "-c", f"core.hooksPath={disabled_hooks}", "clone",
                    "--depth", "1", "--single-branch", "--no-tags",
                    "--filter=blob:none", "--no-checkout", "--progress",
                    safe_url, str(repository_path),
                ),
                timeout=self.config.clone_timeout_seconds,
            )
            clone_ms = clone.duration_ms
            self.git_diagnostics.append(_diagnostic(
                "clone", safe_url, clone.returncode, clone.timed_out,
                clone.stderr or clone.stdout, clone_ms, None,
            ))
            if clone.timed_out or clone.returncode != 0:
                detail = _sanitize_stderr(clone.stderr or clone.stdout, safe_url)
                raise RepositoryCloneError(
                    (f"git clone exceeded {self.config.clone_timeout_seconds:g} seconds"
                     if clone.timed_out else detail or "git clone failed"),
                    diagnostics=self.git_diagnostics,
                )

            checkout = _run_git_stage(
                _git_command("-c", f"core.hooksPath={disabled_hooks}",
                             "-C", str(repository_path), "checkout",
                             "--detach", "--progress", "HEAD"),
                timeout=self.config.checkout_timeout_seconds,
            )
            self.git_diagnostics.append(_diagnostic(
                "checkout", safe_url, checkout.returncode, checkout.timed_out,
                checkout.stderr or checkout.stdout, checkout.duration_ms, None,
            ))
            if checkout.timed_out or checkout.returncode != 0:
                raise RepositoryCloneError(
                    (f"git checkout exceeded {self.config.checkout_timeout_seconds:g} seconds"
                     if checkout.timed_out else "Unable to prepare repository worktree"),
                    diagnostics=self.git_diagnostics,
                )

            commit = _run_git_stage(
                _git_command("-C", str(repository_path), "rev-parse", "HEAD"),
                timeout=self.config.git_verify_timeout_seconds,
            )
            resolved = commit.stdout.strip() if (
                not commit.timed_out and commit.returncode == 0
                and re.fullmatch(r"[0-9a-fA-F]{40}", commit.stdout.strip())
            ) else None
            self.git_diagnostics.append(_diagnostic(
                "head", safe_url, commit.returncode, commit.timed_out,
                commit.stderr or ("" if resolved else commit.stdout),
                commit.duration_ms, resolved,
            ))
            if resolved is None:
                raise RepositoryCloneError(
                    "Unable to resolve cloned repository HEAD",
                    diagnostics=self.git_diagnostics,
                )

            verification = _run_git_stage(
                _git_command("-C", str(repository_path), "status", "--porcelain",
                             "--untracked-files=no"),
                timeout=self.config.git_verify_timeout_seconds,
            )
            worktree_clean = (not verification.timed_out
                              and verification.returncode == 0
                              and not verification.stdout.strip())
            self.git_diagnostics.append(_diagnostic(
                "worktree", safe_url, verification.returncode, verification.timed_out,
                verification.stderr or verification.stdout,
                verification.duration_ms, resolved if worktree_clean else None,
            ))
            if not worktree_clean:
                raise RepositoryCloneError(
                    "Repository worktree verification failed",
                    diagnostics=self.git_diagnostics,
                )
            yield FetchedRepository(
                path=repository_path,
                origin_url=safe_url,
                commit_hash=commit.stdout.strip(),
                clone_ms=clone_ms,
            )
        except RepositoryCloneError as exc:
            primary_error = exc
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
                    if primary_error is None:
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
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1",
                       NO_PROXY="github.com", no_proxy="github.com")
    return environment


def _run_git_stage(command: list[str], *, timeout: float) -> GitStageResult:
    """Run one owned Git stage and bound both execution and timeout cleanup."""
    started = time.perf_counter()
    kwargs = dict(
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", env=_git_environment(),
    )
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except (OSError, ValueError) as exc:
        return GitStageResult(None, "", str(exc), False, _elapsed_ms(started))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return GitStageResult(process.returncode, stdout or "", stderr or "",
                              False, _elapsed_ms(started))
    except subprocess.TimeoutExpired as exc:
        partial_stdout = _decode_output(exc.stdout)
        partial_stderr = _decode_output(exc.stderr)
        _terminate_owned_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            _close_process_pipes(process)
            stdout, stderr = "", ""
        return GitStageResult(
            process.poll(), (stdout or partial_stdout),
            (stderr or partial_stderr), True, _elapsed_ms(started),
        )


def _terminate_owned_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        stopped = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
        )
        if stopped.returncode != 0 and process.poll() is None:
            process.kill()
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _close_process_pipes(process: subprocess.Popen) -> None:
    for stream in (process.stdout, process.stderr, process.stdin):
        if stream is not None:
            stream.close()


def _decode_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _sanitize_stderr(value: object, repository_url: str) -> str:
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
