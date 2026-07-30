from __future__ import annotations

import ctypes
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
LIB_PATH = Path(
    os.environ.get("MOJO_TOMLI_LIB", ROOT / "dist" / "libmojo-tomli.so")
)

I64 = ctypes.c_int64
I64_MAX = (1 << 63) - 1
_lib: ctypes.CDLL | None = None


def lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        if not LIB_PATH.exists():
            raise RuntimeError(
                f"Mojo library not found at {LIB_PATH}; run `pixi run build`"
            )
        _lib = ctypes.CDLL(str(LIB_PATH))
        fn = _lib.mt_parse_primitive_array
        fn.argtypes = [I64] * 9
        fn.restype = I64
    return _lib


def parse_primitive_array(
    source_bytes: np.ndarray, source: str, start: int
) -> tuple[int, list[object]] | None:
    if (
        source_bytes.dtype != np.uint8
        or source_bytes.ndim != 1
        or not source_bytes.flags.c_contiguous
    ):
        raise TypeError("source_bytes must be a contiguous one-dimensional uint8 array")
    if source_bytes.size != len(source):
        raise ValueError("source_bytes and source must have the same length")
    if source_bytes.size > I64_MAX or not 0 <= start < source_bytes.size:
        raise ValueError("source length or start position is outside the FFI range")

    # Find the closing bracket and size the output while ignoring comment text.
    # A plain str.find/count would under-allocate for valid input containing
    # commas or ']' inside a comment.
    close = source.find("]", start + 1)
    if close < 0:
        return None
    first_comment = source.find("#", start + 1, close)
    if first_comment < 0:
        capacity = source.count(",", start + 1, close) + 1
    else:
        pos = first_comment
        commas = source.count(",", start + 1, first_comment)
        while pos < len(source):
            if source[pos] == "#":
                newline = source.find("\n", pos + 1)
                if newline < 0:
                    return None
                pos = newline + 1
                continue
            if source[pos] == "]":
                break
            if source[pos] == ",":
                commas += 1
            pos += 1
        if pos >= len(source):
            return None
        capacity = commas + 1
    kinds = np.empty(capacity, dtype=np.uint8)
    integers = np.zeros(capacity + 1, dtype=np.int64)
    floats = np.empty(capacity, dtype=np.float64)
    starts = np.empty(capacity, dtype=np.int64)
    ends = np.empty(capacity, dtype=np.int64)
    end = lib().mt_parse_primitive_array(
        source_bytes.ctypes.data,
        source_bytes.size,
        start,
        kinds.ctypes.data,
        integers.ctypes.data,
        floats.ctypes.data,
        starts.ctypes.data,
        ends.ctypes.data,
        capacity,
    )
    # -1 means that this is not an eligible primitive array. Other negative
    # statuses indicate an internal contract violation and must not be hidden
    # by the semantic-parser fallback.
    if end == -1:
        return None
    if end < 0:
        raise RuntimeError(f"Mojo array decoder failed with status {end}")
    count = int(integers[capacity])
    if not start < end <= source_bytes.size or not 0 <= count <= capacity:
        raise RuntimeError("Mojo array decoder returned invalid bounds")
    if count and (
        np.any(starts[:count] < start + 1)
        or np.any(ends[:count] < starts[:count])
        or np.any(ends[:count] > end)
        or np.any((kinds[:count] < 1) | (kinds[:count] > 3))
    ):
        raise RuntimeError("Mojo array decoder returned invalid value metadata")
    token_kinds = kinds[:count]
    if count == 0:
        return int(end), []
    if np.all(token_kinds == 1):
        values: list[object] = integers[:count].tolist()
    elif np.all(token_kinds == 2):
        values = [
            float(source[int(starts[i]) : int(ends[i])]) for i in range(count)
        ]
    elif np.all(token_kinds == 3):
        values = [bool(value) for value in integers[:count]]
    else:
        values = [
            int(integers[i])
            if kind == 1
            else float(source[int(starts[i]) : int(ends[i])])
            if kind == 2
            else bool(integers[i])
            for i, kind in enumerate(token_kinds)
        ]
    return int(end), values
