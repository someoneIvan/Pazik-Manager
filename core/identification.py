"""File identification helpers.

The catalogue must distinguish photographs that share a file name (common with
in-camera frame numbering) and survive files being moved or a drive letter
changing.  We combine several cheap signals instead of hashing whole RAW files:

* ``(library_id, path)``         — the primary, authoritative key.
* ``instance_id``                — XMP/EXIF InstanceID / ImageUniqueID when the
  file carries one (stable across renames and moves).
* ``content_hash``               — a *partial* hash (head + tail + size) that is
  fast to compute and good enough to recognise the same image after a move.

The partial hash is computed once at import time and stored; it is never
recomputed on every scan.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: How many bytes to sample from the start and end of a file for the partial
#: hash.  64 KiB from each end plus the file size is plenty to fingerprint a
#: photo while staying essentially O(1) even for large RAW files.
_SAMPLE = 64 * 1024


def partial_hash(path: str | Path) -> str | None:
    """Return a fast, partial content hash for *path*.

    Hashes the first and last :data:`_SAMPLE` bytes together with the file size.
    Returns ``None`` if the file cannot be read.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None

    h = hashlib.sha1()
    h.update(str(size).encode("ascii"))
    try:
        with p.open("rb") as fh:
            head = fh.read(_SAMPLE)
            h.update(head)
            if size > 2 * _SAMPLE:
                fh.seek(-_SAMPLE, 2)
                h.update(fh.read(_SAMPLE))
            elif size > _SAMPLE:
                # Overlap already covered by head; nothing more to read.
                pass
    except OSError:
        return None
    return h.hexdigest()


def file_signature(path: str | Path) -> tuple[int, float] | None:
    """Return ``(size_bytes, mtime)`` for *path*, or ``None`` if missing."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime