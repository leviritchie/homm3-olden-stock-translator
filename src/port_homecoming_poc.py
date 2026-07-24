"""Olden Era .map container read/write (OSS-slimmed).

This module intentionally contains only the gzip+varint JSON chunk container
helpers needed by the stock translator. Campaign LOD / Story_maps paths from the
private monorepo are not included.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = Path(os.environ.get("HOMM3_OLDEN_STOCK_OUT_ROOT", ROOT / "artifacts"))


@dataclass(frozen=True)
class OldenMapContainer:
    version: str
    prefix_suffix: bytes
    chunks: list[dict[str, Any]]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("unterminated Olden varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return value, offset
        shift += 7
        if shift > 35:
            raise ValueError("Olden varint is unexpectedly large")


def write_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("cannot encode a negative Olden varint")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_olden_map_container(path: Path) -> OldenMapContainer:
    raw = gzip.decompress(path.read_bytes())
    if len(raw) < 43 or raw[0:1] != b" ":
        raise ValueError(f"not an expected Olden map container: {path}")

    offset = 1 + 32
    version_length = raw[offset]
    offset += 1
    version = raw[offset : offset + version_length].decode("ascii")
    offset += version_length
    prefix_suffix = raw[offset : offset + 2]
    offset += 2

    chunks: list[dict[str, Any]] = []
    while offset < len(raw):
        chunk_length, offset = read_varint(raw, offset)
        chunk_bytes = raw[offset : offset + chunk_length]
        if len(chunk_bytes) != chunk_length:
            raise ValueError(f"truncated Olden map JSON chunk in {path}")
        chunks.append(json.loads(chunk_bytes))
        offset += chunk_length

    if len(chunks) < 2:
        raise ValueError(f"Olden map has too few JSON chunks: {path}")
    return OldenMapContainer(version=version, prefix_suffix=prefix_suffix, chunks=chunks)


def write_olden_map_container(path: Path, container: OldenMapContainer, hash_sum: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", hash_sum):
        raise ValueError(f"not a 32-character lowercase md5 hash: {hash_sum}")

    raw = bytearray()
    raw.extend(b" ")
    raw.extend(hash_sum.encode("ascii"))
    raw.append(len(container.version))
    raw.extend(container.version.encode("ascii"))
    raw.extend(container.prefix_suffix)
    for chunk in container.chunks:
        chunk_bytes = json.dumps(chunk, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        raw.extend(write_varint(len(chunk_bytes)))
        raw.extend(chunk_bytes)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(bytes(raw), compresslevel=9))
