"""Transversal utilities used across every Capacidad Estrategica (DOC-005).

Moved out of pipeline.py as the first step of M4.1's module decomposition
(Etapa 4, DOC-014/DOC-015 adaptado). No behavior change: every function here
is byte-for-byte the same as before the move -- pipeline.py re-exports all
of it so `import pipeline as p; p.digest(...)` keeps working unmodified.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

_SEMVER_NUMERIC_IDENTIFIER = r"(?:0|[1-9][0-9]*)"
_SEMVER_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_SYSTEM_VERSION_PATTERN = re.compile(
    rf"^{_SEMVER_NUMERIC_IDENTIFIER}\.{_SEMVER_NUMERIC_IDENTIFIER}"
    rf"\.{_SEMVER_NUMERIC_IDENTIFIER}"
    rf"(?:-{_SEMVER_PRERELEASE_IDENTIFIER}(?:\.{_SEMVER_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")
_CODE_REVISION_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def publish(directory, files):
    """Same bytes are a no-op; never overwrite a different or incomplete run."""
    directory = Path(directory)
    if directory.exists():
        if set(p.name for p in directory.iterdir()) != set(files):
            raise ValueError("Output exists with different/incomplete contents")
        if any((directory / name).read_bytes() != data for name, data in files.items()):
            raise ValueError("Output exists with different contents")
        return
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _explicit_utc(value):
    if not isinstance(value, str) or not value or value != value.strip() \
            or not value.endswith("Z"):
        return False
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return instant.utcoffset() == timezone.utc.utcoffset(instant) \
        and instant.isoformat().replace("+00:00", "Z") == value


def _hypothesis_text_is_valid(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def _hypothesis_system_version_is_valid(value):
    """Accept an explicit SemVer value, never a ref or an object identifier."""
    return _hypothesis_text_is_valid(value) and bool(_SYSTEM_VERSION_PATTERN.fullmatch(value))


def _hypothesis_code_revision_is_valid(value):
    """Accept only canonical full Git object identifiers supplied by the caller."""
    return _hypothesis_text_is_valid(value) and bool(_CODE_REVISION_PATTERN.fullmatch(value))


def _pipeline_source_bytes():
    """The exact bytes of the top-level pipeline.py entry point -- the
    reproducibility baseline this project has always captured (README:
    "cada ejecucion conserva una copia exacta de pipeline.py y su
    SHA256"). Resolved from the imported `pipeline` module's own
    __file__, not from wherever this helper itself lives, so every
    Capacidad's materialization keeps hashing pipeline.py -- never its own
    submodule -- no matter which module calls it (M4.1 module
    decomposition, Etapa 4)."""
    import pipeline as _pipeline_module
    return Path(_pipeline_module.__file__).read_bytes().replace(b"\r\n", b"\n")
