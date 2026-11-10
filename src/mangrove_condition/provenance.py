"""Identity of the files used in a run."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from .schema import Fingerprint


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> Fingerprint:
    """Name, hash, size and modification time of a file."""
    path = Path(path)
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Fingerprint(
        file_name=path.name,
        sha256=file_sha256(path),
        size_bytes=stat.st_size,
        modified_utc=modified,
    )
