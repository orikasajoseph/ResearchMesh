from __future__ import annotations

import configparser
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .schemas import Entrypoint, Evidence, RunCommand


_ROOT_PATTERNS = (
    "README*",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "environment*.yml",
    "environment*.yaml",
    "package.json",
    "Dockerfile*",
    "docker-compose*.yml",
    "compose*.yml",
    "Makefile",
    ".env.example",
)
_WORKFLOW_PATTERNS = (".github/workflows/*.yml", ".github/workflows/*.yaml")
_FENCE_LANGUAGES = {"", "bash", "sh", "shell", "console", "powershell", "pwsh", "cmd"}


@dataclass(frozen=True)
class EvidenceCollection:
    evidence: list[Evidence]
    warnings: list[str]


class RepositoryEvidenceCollector:
    def __init__(self, max_file_bytes: int = 64 * 1024) -> None:
        self.max_file_bytes = max_file_bytes

    def collect(self, repo_path: Path, *, start_index: int = 1) -> EvidenceCollection:
        root = repo_path.resolve()
        candidates: dict[str, Path] = {}
        for pattern in (*_ROOT_PATTERNS, *_WORKFLOW_PATTERNS):
            for path in root.glob(pattern):
                if path.is_file():
                    relative = path.relative_to(root).as_posix()
                    candidates[relative] = path

        evidence: list[Evidence] = []
        warnings: list[str] = []
        next_id = start_index
        for relative, path in sorted(candidates.items()):
            if path.is_symlink():
                warnings.append(f"EVIDENCE_SKIPPED_SYMLINK: {relative}")
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                size = resolved.stat().st_size
            except (OSError, ValueError):
                warnings.append(f"EVIDENCE_SKIPPED_UNSAFE_PATH: {relative}")
                continue
            if size > self.max_file_bytes:
                warnings.append(f"EVIDENCE_SKIPPED_OVERSIZED: {relative}")
                continue
            raw = resolved.read_bytes()
            if b"\x00" in raw:
                warnings.append(f"EVIDENCE_SKIPPED_BINARY: {relative}")
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                warnings.append(f"EVIDENCE_SKIPPED_BINARY: {relative}")
                continue
            line_count = max(1, len(text.splitlines()))
            evidence.append(
                Evidence(
                    evidenceId=f"E{next_id}",
                    sourceType=_source_type(relative),
                    filePath=relative,
                    startLine=1,
                    endLine=line_count,
                    text=text,
                    supports=["entrypoints", "runCommands", "reproductionBarriers"],
                )
            )
            next_id += 1
        return EvidenceCollection(evidence=evidence, warnings=warnings)


def extract_declared_metadata(
    evidence: list[Evidence],
) -> tuple[list[Entrypoint], list[RunCommand]]:
    entrypoints: list[Entrypoint] = []
    commands: list[RunCommand] = []
    for item in evidence:
        path = item.filePath
        name = Path(path).name
        lower = name.lower()
        if lower == "pyproject.toml":
            _extract_pyproject(item, entrypoints, commands)
        elif lower == "package.json":
            _extract_package_json(item, entrypoints, commands)
        elif lower == "setup.cfg":
            _extract_setup_cfg(item, entrypoints, commands)
        elif lower == "setup.py":
            _extract_setup_py(item, entrypoints, commands)
        elif name == "Makefile":
            _extract_makefile(item, entrypoints, commands)
        elif lower.startswith("dockerfile"):
            _extract_dockerfile(item, entrypoints, commands)
        elif path.startswith(".github/workflows/"):
            _extract_ci_commands(item, entrypoints, commands)
        elif lower.startswith("readme"):
            _extract_readme_commands(item, commands)
    return _unique_entrypoints(entrypoints), _unique_commands(commands)


def _source_type(relative: str) -> str:
    lower = relative.lower()
    if lower.startswith("readme"):
        return "readme"
    if lower.startswith(".github/workflows/"):
        return "ci"
    if "docker" in lower or lower.startswith("compose"):
        return "container_config"
    if lower in {"pyproject.toml", "setup.py", "setup.cfg", "package.json", "makefile"}:
        return "manifest"
    if lower == ".env.example":
        return "environment_example"
    return "dependency_config"


def _add_script(
    item: Evidence,
    name: str,
    target: str,
    kind: str,
    command: str,
    entrypoints: list[Entrypoint],
    commands: list[RunCommand],
) -> None:
    entrypoints.append(
        Entrypoint(
            name=name,
            entrypointType=kind,
            target=target,
            source=item.filePath,
            evidenceIds=[item.evidenceId],
        )
    )
    commands.append(
        RunCommand(
            command=command,
            source=item.filePath,
            evidenceIds=[item.evidenceId],
        )
    )


def _extract_pyproject(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    try:
        data = tomllib.loads(item.text)
    except (tomllib.TOMLDecodeError, TypeError):
        return
    groups = (
        (data.get("project", {}).get("scripts", {}), "pyproject-script"),
        (data.get("tool", {}).get("poetry", {}).get("scripts", {}), "poetry-script"),
    )
    for scripts, kind in groups:
        if not isinstance(scripts, dict):
            continue
        for name, target in sorted(scripts.items()):
            if isinstance(name, str) and isinstance(target, str):
                _add_script(item, name, target, kind, name, entrypoints, commands)


def _extract_package_json(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    try:
        data = json.loads(item.text)
    except (json.JSONDecodeError, TypeError):
        return
    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return
    for name, target in sorted(scripts.items()):
        if isinstance(name, str) and isinstance(target, str):
            _add_script(
                item,
                name,
                target,
                "package-script",
                f"npm run {name}",
                entrypoints,
                commands,
            )


def _extract_setup_cfg(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(item.text)
    except configparser.Error:
        return
    if not parser.has_section("options.entry_points"):
        return
    raw = parser.get("options.entry_points", "console_scripts", fallback="")
    for line in raw.splitlines():
        if "=" not in line:
            continue
        name, target = (part.strip() for part in line.split("=", 1))
        if name and target:
            _add_script(item, name, target, "console-script", name, entrypoints, commands)


def _extract_setup_py(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    for name, target in re.findall(
        r"['\"]([A-Za-z0-9_.-]+)\s*=\s*([A-Za-z0-9_.:-]+)['\"]", item.text
    ):
        _add_script(item, name, target, "console-script", name, entrypoints, commands)


def _extract_makefile(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    for target in re.findall(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", item.text):
        if target.startswith("."):
            continue
        _add_script(
            item,
            target,
            target,
            "make-target",
            f"make {target}",
            entrypoints,
            commands,
        )


def _extract_dockerfile(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    for instruction, value in re.findall(r"(?im)^\s*(ENTRYPOINT|CMD)\s+(.+)$", item.text):
        entrypoints.append(
            Entrypoint(
                name=instruction.upper(),
                entrypointType="docker-instruction",
                target=value.strip(),
                source=item.filePath,
                evidenceIds=[item.evidenceId],
            )
        )
        commands.append(
            RunCommand(
                command=value.strip(), source=item.filePath, evidenceIds=[item.evidenceId]
            )
        )


def _extract_ci_commands(
    item: Evidence, entrypoints: list[Entrypoint], commands: list[RunCommand]
) -> None:
    lines = item.text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)run:\s*(.*)$", line)
        if not match:
            continue
        indent, value = len(match.group(1)), match.group(2).strip()
        values: list[str] = []
        if value and value not in {"|", ">", "|-", ">-"}:
            values.append(value)
        else:
            for following in lines[index + 1 :]:
                stripped = following.strip()
                current_indent = len(following) - len(following.lstrip())
                if stripped and current_indent <= indent:
                    break
                if stripped and not stripped.startswith("#"):
                    values.append(stripped)
        for command in values:
            commands.append(
                RunCommand(command=command, source=item.filePath, evidenceIds=[item.evidenceId])
            )
            entrypoints.append(
                Entrypoint(
                    name=f"ci-run-{index + 1}",
                    entrypointType="ci-command",
                    target=command,
                    source=item.filePath,
                    evidenceIds=[item.evidenceId],
                )
            )


def _extract_readme_commands(item: Evidence, commands: list[RunCommand]) -> None:
    in_fence = False
    accepted = False
    for line in item.text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                language = stripped[3:].strip().lower()
                accepted = language in _FENCE_LANGUAGES
                in_fence = True
            else:
                in_fence = False
                accepted = False
            continue
        if not in_fence or not accepted or not stripped or stripped.startswith("#"):
            continue
        command = re.sub(r"^(?:\$|PS>)\s*", "", stripped)
        if command:
            commands.append(
                RunCommand(command=command, source=item.filePath, evidenceIds=[item.evidenceId])
            )


def _unique_entrypoints(values: list[Entrypoint]) -> list[Entrypoint]:
    unique: dict[tuple[str, str, str, str], Entrypoint] = {}
    for value in values:
        key = (value.name, value.entrypointType, value.target, value.source)
        unique.setdefault(key, value)
    return list(unique.values())


def _unique_commands(values: list[RunCommand]) -> list[RunCommand]:
    unique: dict[tuple[str, str], RunCommand] = {}
    for value in values:
        unique.setdefault((value.command, value.source), value)
    return list(unique.values())
