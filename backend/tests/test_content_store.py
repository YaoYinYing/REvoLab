"""ContentStore: immutable, content-addressed byte identity via fsspec."""

import hashlib

import pytest

from revolab.content_store import ContentStore
from revolab.domain.errors import ConflictError, NotFoundError


def test_put_get_round_trip_and_identity(tmp_path):
    store = ContentStore(tmp_path / "content")
    data = b"ACGTACGT"
    result = store.put(data, content_type="text/plain")
    assert result["checksum"] == hashlib.sha256(data).hexdigest()
    assert result["size"] == 8
    assert result["content_type"] == "text/plain"
    assert store.get(str(result["store_handle"])) == data


def test_same_bytes_same_checksum_and_no_mutation(tmp_path):
    store = ContentStore(tmp_path / "content")
    first = store.put(b"immutable")
    second = store.put(b"immutable")
    assert first["checksum"] == second["checksum"]
    assert store.get(str(first["store_handle"])) == b"immutable"


def test_get_missing_raises_not_found(tmp_path):
    store = ContentStore(tmp_path / "content")
    with pytest.raises(NotFoundError):
        store.get("0" * 64)


def test_read_range_is_bounded(tmp_path):
    store = ContentStore(tmp_path / "content")
    data = b"0123456789abcdef"
    result = store.put(data)
    assert store.read_range(str(result["store_handle"]), offset=0, limit=4) == b"0123"
    assert store.read_range(str(result["store_handle"]), offset=4, limit=6) == b"456789"


def test_stored_bytes_are_immutable_and_integrity_checked(tmp_path):
    store = ContentStore(tmp_path / "content")
    result = store.put(b"real-bytes")
    checksum = str(result["checksum"])
    # Tamper with the stored bytes out-of-band: get must fail the integrity check.
    store._fs.pipe(store._path(checksum), b"tampered")
    with pytest.raises(ConflictError):
        store.get(checksum)


class _StallingHandle:
    """A file handle that writes one byte, signals, then stalls before the rest."""

    def __init__(self, handle, entered, release) -> None:
        self._handle = handle
        self._entered = entered
        self._release = release

    def write(self, data: bytes) -> int:
        self._handle.write(data[:1])
        self._entered.set()
        self._release.wait(timeout=10)
        self._handle.write(data[1:])
        return len(data)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):  # type: ignore[no-untyped-def]
        return self._handle.__exit__(*exc)


class _StallingFileSystem:
    """Delegates to the real filesystem but stalls the FIRST binary write mid-file."""

    def __init__(self, inner, entered, release) -> None:
        self._inner = inner
        self._entered = entered
        self._release = release
        self._stalled = False

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)

    def open(self, path, mode="rb", **kwargs):  # type: ignore[no-untyped-def]
        handle = self._inner.open(path, mode, **kwargs)
        if mode == "wb" and not self._stalled:
            self._stalled = True
            return _StallingHandle(handle, self._entered, self._release)
        return handle


def test_a_partial_concurrent_write_is_never_observable(tmp_path, monkeypatch):
    """A content-addressed put is ATOMIC under concurrency (deterministic).

    Two importers can legitimately store the SAME byte-identical snapshot at the same
    time. A non-atomic check-then-`open(path, "wb")` lets the destination exist in a
    TRUNCATED state, so a concurrent writer's verification read sees a prefix and raises
    a spurious `ConflictError("duplicate checksum with different bytes")`. Staging into a
    private temporary path and renaming it into place makes a reader observe only
    "absent" or the complete file.

    The first writer is stalled mid-write so the interleaving is DETERMINISTIC: with the
    atomic put the second writer never sees a partial destination, and both succeed.
    """
    import threading

    store = ContentStore(tmp_path / "content")
    data = b"data_CIF\n" + b"z" * 4096
    checksum = hashlib.sha256(data).hexdigest()
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(store, "_fs", _StallingFileSystem(store._fs, entered, release))

    errors: list[BaseException] = []

    def first_writer() -> None:
        try:
            store.put(data, content_type="chemical/x-cif")
        except BaseException as exc:  # pragma: no cover - reported by the assertion
            errors.append(exc)

    thread = threading.Thread(target=first_writer)
    thread.start()
    try:
        assert entered.wait(timeout=10)
        # A concurrent second writer of the SAME bytes completes while the first is
        # mid-write. Under a non-atomic put this reads a truncated file and raises.
        second = store.put(data, content_type="chemical/x-cif")
        assert second["checksum"] == checksum
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    # Exactly ONE immutable file, no temporary leftovers, and intact bytes.
    stored = [path for path in (tmp_path / "content").rglob("*") if path.is_file()]
    assert len(stored) == 1
    assert store.get(checksum) == data
