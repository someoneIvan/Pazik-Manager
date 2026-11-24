"""Shared pytest fixtures.

Each test session gets an isolated data directory (via ``PAZIK_DATA_DIR``) so
the real user catalogue is never touched.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_data_dir():
    tmp = tempfile.mkdtemp(prefix="pazik_test_")
    os.environ["PAZIK_DATA_DIR"] = tmp
    yield tmp


@pytest.fixture()
def catalog(tmp_path):
    from core.catalog import Catalog

    cat = Catalog(tmp_path / "test.db")
    yield cat
    cat.close()


def make_jpeg(path: Path, size=(800, 600), color=(120, 160, 200),
              exif_datetime: str | None = None) -> Path:
    """Create a small JPEG, optionally with a DateTimeOriginal EXIF tag."""
    from PIL import Image

    img = Image.new("RGB", size, color)
    save_kwargs = {}
    if exif_datetime:
        exif = Image.Exif()
        # 36867 == DateTimeOriginal
        exif[36867] = exif_datetime
        save_kwargs["exif"] = exif
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", **save_kwargs)
    return path


def make_preview_jpeg(size=(1600, 1000), color=(40, 90, 160)) -> bytes:
    """Return JPEG bytes of a large image (used as a fake RAW preview)."""
    from PIL import Image

    img = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


FAKE_RAW_TAGS = {
    "SourceFile": "IMG_0001.CR2",
    "DateTimeOriginal": "2021:06:07 08:09:10",
    "Make": "Canon",
    "Model": "Canon EOS 5D Mark IV",
    "ImageWidth": "6720",
    "ImageHeight": "4480",
    "FNumber": "4.0",
    "ExposureTime": "1/250",
    "ISO": 200,
    "LensModel": "EF 24-70mm f/2.8L II USM",
    "ImageUniqueID": "ABC123DEF456",
}

_PREVIEW_TAGS = (
    "PreviewImage",
    "JpgFromRaw",
    "OtherImage",
    "ThumbnailImage",
    "PhotoshopThumbnail",
)


@pytest.fixture()
def fake_exiftool(monkeypatch):
    """Install a fake ``exiftool`` binary and return its mutable state.

    ``state["tags"]`` is the JSON payload; ``state["previews"]`` maps tag names
    to raw bytes.  The returned namespace lets tests opt into fallback paths.
    """
    from core import exiftool

    state = SimpleNamespace(
        tags=dict(FAKE_RAW_TAGS),
        previews={"PreviewImage": make_preview_jpeg()},
        calls=[],
    )

    monkeypatch.setattr(exiftool, "_cached_path", "/fake/exiftool")
    monkeypatch.setattr(exiftool, "_cached_resolved", True)

    def fake_run(cmd, **kwargs):
        state.calls.append(list(cmd))
        if "-json" in cmd:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([state.tags]).encode("utf-8"),
                stderr=b"",
            )
        if "-b" in cmd:
            tag = next(
                (
                    arg[1:]
                    for arg in cmd
                    if arg.startswith("-") and arg[1:] in _PREVIEW_TAGS
                ),
                None,
            )
            data = state.previews.get(tag, b"") if tag else b""
            return SimpleNamespace(returncode=0, stdout=data, stderr=b"")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"unsupported")

    monkeypatch.setattr(exiftool.subprocess, "run", fake_run)
    return state


def make_fake_raw(path: Path) -> Path:
    """Create a file with a RAW extension (contents are irrelevant to fakes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"II*\x00" + b"\x00" * 512)
    return path