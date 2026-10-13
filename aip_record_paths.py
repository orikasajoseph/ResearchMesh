"""Validate owned local AIP records without permitting arbitrary output writes."""
from pathlib import Path


def require_record_directory(path, project_root, stage):
    """Accept legacy runs or a single-entry stage, never outputs/cache or escapes.

    Resolve symlinks/junctions before comparing paths. The single-entry layout is
    outputs/aip-research-<unique-run>/<literature|reader>; other nested layouts
    are not silently enabled. The caller must have created the directory first.
    """
    output = Path(path).resolve()
    base = (Path(project_root).resolve() / "outputs").resolve()
    if stage not in ("literature", "reader") or not output.is_dir():
        raise ValueError("INVALID_AIP_RECORD_DIRECTORY")
    try:
        parts = output.relative_to(base).parts
    except ValueError:
        raise ValueError("INVALID_AIP_RECORD_DIRECTORY") from None
    legacy = len(parts) == 1 and parts[0] != "pdf-cache"
    nested = (len(parts) == 2 and parts[0].startswith("aip-research-")
              and len(parts[0]) > len("aip-research-") and parts[1] == stage)
    if not (legacy or nested):
        raise ValueError("INVALID_AIP_RECORD_DIRECTORY")
    return output
