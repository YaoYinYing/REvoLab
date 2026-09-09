"""ContentStore — the internal byte-ownership boundary (fsspec local backend).

Immutable, content-addressed `put`/`get` for bytes that do not come from an
external engine (authority = revolab). This is a byte boundary, not a file
server: bytes are immutable, addressed by checksum, and sized/typed at put time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypedDict, cast

import fsspec  # type: ignore[import-untyped]

from revolab.domain.errors import ConflictError, NotFoundError, ValidationError


class PutResult(TypedDict):
    checksum: str
    size: int
    content_type: str | None
    store_handle: str


class ContentStore:
    def __init__(self, root: str | Path) -> None:
        self._root = str(root)
        self._fs: fsspec.AbstractFileSystem = fsspec.filesystem("file")
        self._fs.makedirs(self._root, exist_ok=True)

    def _path(self, checksum: str) -> str:
        # Two-level sharding keeps a single directory from becoming enormous.
        return f"{self._root}/{checksum[:2]}/{checksum}"

    def put(self, data: bytes, *, content_type: str | None = None) -> PutResult:
        checksum = hashlib.sha256(data).hexdigest()
        size = len(data)
        path = self._path(checksum)
        self._fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
        if not self._fs.exists(path):
            with self._fs.open(path, "wb") as handle:
                handle.write(data)
        else:
            # Content-addressed: same checksum must mean same bytes.
            existing = self._fs.cat(path)
            if existing != data:
                raise ConflictError("duplicate checksum with different bytes")
        return {
            "checksum": checksum,
            "size": size,
            "content_type": content_type,
            "store_handle": checksum,
        }

    def get(self, store_handle: str) -> bytes:
        if "/" in store_handle or ".." in store_handle:
            raise ValidationError("invalid store handle")
        path = self._path(store_handle)
        if not self._fs.exists(path):
            raise NotFoundError("content not found")
        data = cast(bytes, self._fs.cat(path))
        if hashlib.sha256(data).hexdigest() != store_handle:
            raise ConflictError("stored bytes failed integrity check")
        return data

    def read_range(self, store_handle: str, *, offset: int = 0, limit: int) -> bytes:
        """Bounded stream-like read (Phase 6 artifact preview): read at most
        `limit` bytes starting at `offset` without materializing the whole stored
        artifact. Integrity of the requested slice is still content-addressed by
        construction (the handle is the full-content checksum)."""
        if "/" in store_handle or ".." in store_handle:
            raise ValidationError("invalid store handle")
        if offset < 0 or limit < 0:
            raise ValidationError("offset and limit must be non-negative")
        path = self._path(store_handle)
        if not self._fs.exists(path):
            raise NotFoundError("content not found")
        with self._fs.open(path, "rb") as handle:
            handle.seek(offset)
            return cast(bytes, handle.read(limit))
