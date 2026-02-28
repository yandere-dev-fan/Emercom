from __future__ import annotations

import struct
import zlib
from typing import Sequence


def empty_cells(width: int, height: int) -> list[int]:
    return [0] * (width * height)


def encode_cells(cells: Sequence[int]) -> bytes:
    if not cells:
        return zlib.compress(b"")
    packed = struct.pack(f"<{len(cells)}H", *cells)
    return zlib.compress(packed, level=6)


def decode_cells(blob: bytes, expected_count: int) -> list[int]:
    raw = zlib.decompress(blob)
    if expected_count == 0:
        return []
    expected_size = expected_count * 2
    if len(raw) != expected_size:
        raise ValueError("Layer payload size does not match map dimensions.")
    return list(struct.unpack(f"<{expected_count}H", raw))
