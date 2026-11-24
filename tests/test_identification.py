"""Tests for file identification helpers."""

from __future__ import annotations

from core import identification
from tests.conftest import make_jpeg


def test_partial_hash_is_deterministic(tmp_path):
    img = make_jpeg(tmp_path / "a.jpg")
    h1 = identification.partial_hash(img)
    h2 = identification.partial_hash(img)
    assert h1 is not None
    assert h1 == h2


def test_partial_hash_changes_with_content(tmp_path):
    a = make_jpeg(tmp_path / "a.jpg", color=(10, 10, 10))
    b = make_jpeg(tmp_path / "b.jpg", color=(250, 250, 250))
    assert identification.partial_hash(a) != identification.partial_hash(b)


def test_partial_hash_missing_file(tmp_path):
    assert identification.partial_hash(tmp_path / "nope.jpg") is None


def test_file_signature(tmp_path):
    img = make_jpeg(tmp_path / "a.jpg")
    sig = identification.file_signature(img)
    assert sig is not None
    size, mtime = sig
    assert size > 0
    assert mtime > 0


def test_file_signature_missing(tmp_path):
    assert identification.file_signature(tmp_path / "missing.jpg") is None