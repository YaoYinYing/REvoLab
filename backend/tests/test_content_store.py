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


def test_stored_bytes_are_immutable_and_integrity_checked(tmp_path):
    store = ContentStore(tmp_path / "content")
    result = store.put(b"real-bytes")
    checksum = str(result["checksum"])
    # Tamper with the stored bytes out-of-band: get must fail the integrity check.
    store._fs.pipe(store._path(checksum), b"tampered")
    with pytest.raises(ConflictError):
        store.get(checksum)
