#!/usr/bin/env python3
"""Tests for the OTA compression layer (deploy/ota/ota_compression.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "ota"))

import ota_compression as oc  # noqa: E402


def _codec_available(codec: str) -> bool:
    return codec in oc.available_codecs()


def _make_payload(tmp_path: Path, size: int = 20000) -> Path:
    src = tmp_path / "payload.bin"
    data = bytearray()
    pattern = b"aion-ota-"
    while len(data) < size:
        data.extend(pattern)
    src.write_bytes(bytes(data))
    return src


@pytest.mark.parametrize("codec", ["zst", "lz4", "xz"])
def test_roundtrip_per_codec(tmp_path, codec):
    if not _codec_available(codec):
        pytest.skip(f"{codec} backend not available on this host")
    src = _make_payload(tmp_path)
    comp = tmp_path / f"payload.{codec}"
    back = tmp_path / "payload.out"

    assert oc.compress(src, comp, codec) is True
    assert comp.exists()
    assert comp.stat().st_size < src.stat().st_size

    assert oc.decompress(comp, back) is True
    assert back.read_bytes() == src.read_bytes()


def test_roundtrip_zst_default(tmp_path):
    if not _codec_available("zst"):
        pytest.skip("zstd backend not available on this host")
    src = _make_payload(tmp_path)
    comp = tmp_path / "payload.zst"
    back = tmp_path / "payload.out"

    assert oc.compress(src, comp) is True
    assert oc.decompress(comp, back) is True
    assert back.read_bytes() == src.read_bytes()


def test_decompress_unknown_suffix_copies_through(tmp_path):
    src = _make_payload(tmp_path)
    plain = tmp_path / "payload.raw"
    plain.write_bytes(src.read_bytes())
    dest = tmp_path / "dest.bin"
    assert oc.decompress(plain, dest) is True
    assert dest.read_bytes() == src.read_bytes()


def test_compress_missing_source_returns_false(tmp_path):
    missing = tmp_path / "nope.bin"
    dest = tmp_path / "nope.zst"
    assert oc.compress(missing, dest) is False
    assert not dest.exists()


def test_decompress_missing_archive_returns_false(tmp_path):
    dest = tmp_path / "out.bin"
    assert oc.decompress(tmp_path / "missing.zst", dest) is False


def test_is_supported(tmp_path):
    assert oc.is_supported("zst") is True
    assert oc.is_supported(".zstd") is True
    assert oc.is_supported(".lz4") is True
    assert oc.is_supported(".xz") is True
    assert oc.is_supported("tar.gz") is False
    assert oc.is_supported("") is False


def test_available_codecs_never_empty(tmp_path):
    assert isinstance(oc.available_codecs(), list)
    assert "zst" in oc.available_codecs()  # zstandard lib ships everywhere we care


def test_decompress_does_not_delete_archive(tmp_path):
    if not _codec_available("zst"):
        pytest.skip("zstd backend not available on this host")
    src = _make_payload(tmp_path)
    comp = tmp_path / "payload.zst"
    oc.compress(src, comp, "zst")
    oc.decompress(comp, tmp_path / "payload.out")
    assert comp.exists()  # original archive preserved
