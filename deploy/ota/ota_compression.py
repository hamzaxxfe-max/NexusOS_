#!/usr/bin/env python3
"""Aion OTA compression layer (ZSTD / LZ4 / XZ).

Provides real-time (de)compression for the update stream so that network
payloads are minimised. Supports both Python libraries (zstandard, lz4)
and system CLIs (zstd, lz4, xz). Every operation checks that the backing
tool actually exists before running, so the module degrades gracefully on
minimal systems without ever losing the original file.
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("aion-ota-compression")

SUPPORTED = {"zst", "lz4", "xz", "zstd", "lz4"}

try:
    import zstandard as _zstd
    HAVE_ZSTD_LIB = True
except ImportError:
    HAVE_ZSTD_LIB = False

try:
    import lz4.frame as _lz4_frame
    HAVE_LZ4_LIB = True
except ImportError:
    HAVE_LZ4_LIB = False


def _has_cli(name: str) -> bool:
    return shutil.which(name) is not None


def available_codecs() -> list[str]:
    """Return codecs actually usable on this host (library or CLI)."""
    codecs = []
    if HAVE_ZSTD_LIB or _has_cli("zstd"):
        codecs.append("zst")
    if HAVE_LZ4_LIB or _has_cli("lz4"):
        codecs.append("lz4")
    if _has_cli("xz"):
        codecs.append("xz")
    return codecs


def is_supported(suffix: str) -> bool:
    return (suffix or "").lstrip(".") in SUPPORTED


def compress(src: Path, dest: Path, codec: str = "zst", level: int = 3) -> bool:
    """Compress src into dest. Returns False on any failure (src untouched)."""
    src = Path(src)
    dest = Path(dest)
    codec = codec.lstrip(".").lower()
    if not src.exists():
        logger.error("compress: source missing %s", src)
        return False

    try:
        if codec == "zst":
            if HAVE_ZSTD_LIB:
                cctx = _zstd.ZstdCompressor(level=level)
                with open(src, "rb") as fin, open(dest, "wb") as fout:
                    cctx.copy_stream(fin, fout)
                return True
            if _has_cli("zstd"):
                return _run(["zstd", "-q", "-f", f"-{level}", str(src), "-o", str(dest)])
        elif codec == "lz4":
            if HAVE_LZ4_LIB:
                with open(src, "rb") as fin, open(dest, "wb") as fout:
                    fout.write(_lz4_frame.compress(fin.read(), compression_level=level))
                return True
            if _has_cli("lz4"):
                return _run(["lz4", "-q", "-f", str(src), str(dest)])
        elif codec == "xz":
            if _has_cli("xz"):
                return _run(["xz", "-k", "-f", "-T0", str(src), "-c"], out=dest)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("compress %s failed: %s", codec, e)

    logger.error("compress: no usable backend for %s", codec)
    return False


def decompress(src: Path, dest: Path) -> bool:
    """Decompress src (auto-detected by suffix) into dest.

    The original archive is never deleted.
    """
    src = Path(src)
    dest = Path(dest)
    if not src.exists():
        logger.error("decompress: archive missing %s", src)
        return False

    suffix = src.suffix.lower().lstrip(".")
    if suffix in ("zst", "zstd"):
        if HAVE_ZSTD_LIB:
            try:
                dctx = _zstd.ZstdDecompressor()
                with open(src, "rb") as fin, open(dest, "wb") as fout:
                    dctx.copy_stream(fin, fout)
                return True
            except Exception as e:
                logger.error("zstd lib decompress failed: %s", e)
        if _has_cli("zstd"):
            return _run(["zstd", "-q", "-d", "-f", str(src), "-o", str(dest)])
    elif suffix == "lz4":
        if HAVE_LZ4_LIB:
            try:
                with open(src, "rb") as fin:
                    data = _lz4_frame.decompress(fin.read())
                dest.write_bytes(data)
                return True
            except Exception as e:
                logger.error("lz4 lib decompress failed: %s", e)
        if _has_cli("lz4"):
            return _run(["lz4", "-q", "-d", "-f", str(src), str(dest)])
    elif suffix == "xz":
        if _has_cli("xz"):
            return _run(["xz", "-k", "-d", "-f", str(src), "-c"], out=dest)
    else:
        # Not compressed: copy through (behaviour preserved).
        shutil.copy2(src, dest)
        return True

    logger.error("decompress: no usable backend for .%s", suffix)
    return False


def _run(cmd: list[str], out: Path | None = None) -> bool:
    try:
        kw = {}
        if out is not None:
            kw["stdout"] = open(out, "wb")
        result = subprocess.run(cmd, capture_output=True, timeout=900, **kw)
        if out is not None:
            kw["stdout"].close()
        if result.returncode != 0:
            logger.error("command failed %s: %s", cmd, result.stderr[:300])
            if out is not None and out.exists():
                out.unlink(missing_ok=True)
            return False
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error("command error %s: %s", cmd, e)
        if out is not None and out.exists():
            out.unlink(missing_ok=True)
        return False


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aion OTA compression utility")
    parser.add_argument("action", choices=["compress", "decompress", "codecs"])
    parser.add_argument("src", nargs="?", help="source file")
    parser.add_argument("dest", nargs="?", help="destination file")
    parser.add_argument("--codec", default="zst", help="zst | lz4 | xz")
    args = parser.parse_args()

    if args.action == "codecs":
        print(",".join(available_codecs()))
        return 0

    if not args.src or not args.dest:
        parser.error(f"{args.action} requires src and dest")

    src, dest = Path(args.src), Path(args.dest)
    if args.action == "compress":
        return 0 if compress(src, dest, args.codec) else 1
    return 0 if decompress(src, dest) else 1


if __name__ == "__main__":
    sys.exit(main())
