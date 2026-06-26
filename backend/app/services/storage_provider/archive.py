"""
Archive export — build a ZIP of the storage tree, backend-agnostic.

Walks the active StorageProvider (local disk, S3, …) via its
managers → employees → months → items interface and streams each file into a
ZIP, so download works the same whether files live on disk or in S3.

Two entry points:
  iter_zip(scope)  -> generator of ZIP byte chunks  (use this for downloads)
  build_zip(scope) -> bytes                          (small/known-small scopes)

`iter_zip` is the scalable path: it never holds the whole archive in memory —
each source file is added then its bytes are flushed to the client immediately.
A 5 GB or 50 GB vault streams with memory bounded to roughly one file at a time,
so the request can't OOM the backend.
"""
from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator

from app.services.storage_provider import get_storage_provider

# Month folders are named "<Month>-<Year>" (e.g. "May-2026"); pull the year from
# the month segment of a vault path "<Manager>/<Employee>/<Month-Year>/<file>".
_YEAR_RE = re.compile(r"-(\d{4})$")


def _year_of(zip_path: str) -> int | None:
    parts = zip_path.split("/")
    if len(parts) < 4:
        return None
    m = _YEAR_RE.search(parts[-2])
    return int(m.group(1)) if m else None


class _ChunkBuffer(io.RawIOBase):
    """A write-only, NON-seekable sink. Because it exposes no seek()/tell(),
    zipfile writes streaming-friendly entries (data descriptors, Zip64), and we
    drain whatever bytes have accumulated after each file."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self._parts.append(bytes(b))
        return len(b)

    def drain(self) -> bytes:
        if not self._parts:
            return b""
        out = b"".join(self._parts)
        self._parts.clear()
        return out


def _matches(zip_path: str, prefix: str | None, year: int | None) -> bool:
    if prefix and not (zip_path == prefix or zip_path.startswith(prefix + "/")):
        return False
    if year is not None and _year_of(zip_path) != year:
        return False
    return True


def iter_zip(
    manager: str | None = None,
    rel_prefix: str | None = None,
    year: int | None = None,
) -> Iterator[bytes]:
    """Yield the vault as a stream of ZIP byte chunks.

    manager     — limit to one account-manager subtree (fast S3 prefix scan).
    rel_prefix  — limit to any subtree by vault-relative path, e.g.
                  '<Manager>/<Employee>' or '<Manager>/<Employee>/<Month-Year>'.
    year        — limit to a calendar year (all managers/employees/months whose
                  Month-Year folder ends in that year). Keeps each export bounded
                  (a year is ≈ 5 GB at 600 employees) so it never gets unwieldy.

    Stored (uncompressed) entries: vault files are PDFs/images that don't
    compress, so we skip deflate to save CPU and keep the stream fast.
    """
    sp = get_storage_provider()
    sink = _ChunkBuffer()
    prefix = rel_prefix.strip("/") if rel_prefix else None
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for zip_path, data in sp.iter_files(manager):
            if not _matches(zip_path, prefix, year):
                continue
            zf.writestr(zip_path, data)
            chunk = sink.drain()
            if chunk:
                yield chunk
    tail = sink.drain()  # central directory is written on close
    if tail:
        yield tail


def year_summary() -> list[dict]:
    """Per-year file count + total bytes across the whole vault (metadata only —
    no downloads). Drives the year-wise download dropdown. Sorted newest first."""
    sp = get_storage_provider()
    agg: dict[int, list[int]] = {}        # year -> [files, bytes]
    for zip_path, size in sp.iter_file_meta():
        yr = _year_of(zip_path)
        if yr is None:
            continue
        slot = agg.setdefault(yr, [0, 0])
        slot[0] += 1
        slot[1] += size
    return [{"year": y, "files": v[0], "bytes": v[1]}
            for y, v in sorted(agg.items(), reverse=True)]


def scope_size(manager: str | None = None, rel_prefix: str | None = None,
               year: int | None = None) -> dict:
    """Total (files, bytes) of a download scope — so the client can show an
    accurate progress bar before/while streaming. Metadata only."""
    sp = get_storage_provider()
    prefix = rel_prefix.strip("/") if rel_prefix else None
    files = total = 0
    for zip_path, size in sp.iter_file_meta(manager):
        if not _matches(zip_path, prefix, year):
            continue
        files += 1
        total += size
    return {"files": files, "bytes": total}


def build_zip(manager: str | None = None) -> bytes:
    """Whole archive (or one manager) as bytes. Prefer iter_zip() for downloads;
    kept for callers that need the full bytes of a known-small scope."""
    return b"".join(iter_zip(manager))
