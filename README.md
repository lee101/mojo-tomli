# mojo-tomli

`mojo-tomli` is a standalone TOML parser with a Python API compatible with
[`tomli`](https://github.com/hukkin/tomli) and a Mojo fast path for primitive arrays.
It is useful for configuration files in general, and particularly for
machine-generated TOML containing large numeric or boolean arrays.

```python
import mojo_tomli as tomli

config = tomli.loads("""
title = "simulation"
steps = 1000
weights = [0.125, 0.25, 0.5, 1.0]

[output]
enabled = true
format = "binary"
""")

assert config["weights"][2] == 0.5
assert config["output"]["enabled"] is True
```

## Supported surface

The public parsing API and TOML values supported by tomli are covered:

- `loads(str, *, parse_float=float)`
- `load(binary_file, *, parse_float=float)`
- `TOMLDecodeError`, including `msg`, `doc`, `pos`, `lineno`, and `colno`
- bare, dotted, quoted, and empty keys
- standard tables, arrays of tables, and inline tables
- basic, literal, multiline basic, and multiline literal strings
- booleans, decimal/base-prefixed integers, normal and special floats
- local dates, local times, local datetimes, and offset datetimes
- nested and heterogeneous arrays, comments, and custom `parse_float` callables

The accepted syntax and returned Python values match tomli 2.4.1, including its TOML
1.1 additions such as `\x`, `\e`, multiline inline tables, and trailing inline-table
commas. The committed suite has 69 tests; a differential audit on this machine against tomli's bundled
TOML corpus additionally matched all 228 valid and 516 invalid documents.

This project only parses TOML. It does not write TOML, preserve formatting/comments,
or replace `tomli-w`. Mojo acceleration currently applies to ASCII, flat arrays made
of booleans, integers, and floats when the default `float` converter is used. Nested
arrays, strings, date/time values, non-ASCII documents, and custom float converters
remain fully supported by the Python semantic parser, but do not use that fast path.

## Install

The repository uses Pixi and the pinned Mojo nightly:

```bash
pixi install
pixi run build
pixi run test
```

Pixi sets `PYTHONPATH=python`. After building, the example at the top of this README
can be run with `pixi run python example.py`, or code can import `mojo_tomli` from any
Pixi task. The compiled library is written to `dist/libmojo-tomli.so`. This release
targets Linux x86-64; it is not packaged as a wheel.

## Benchmarks

Measured with `pixi run bench` on an Intel Xeon E5-2697 v4 at 2.30 GHz, x86-64 Linux,
using Python 3.13.14.

| case | mojo-tomli | tomli 2.4.1 | ratio | result |
| --- | ---: | ---: | ---: | --- |
| tiny config (4 scalars) | 0.025 ms | 0.028 ms | 1.10x | faster |
| table (5,000 string keys) | 19.132 ms | 37.192 ms | 1.94x | faster |
| integer array (250,000) | 41.144 ms | 658.184 ms | 16.00x | faster |
| float array (150,000) | 158.829 ms | 785.837 ms | 4.95x | faster |
| commented integer array (75,000) | 168.989 ms | 266.855 ms | 1.58x | faster |

Simple keys and escape-free strings use guarded parser fast paths, and the NumPy source
view is created lazily only when an eligible array is encountered. Numbers are
best-of-five timings after warm-up; the tiny case is best-of-twenty.

No SIMD, parallel, or GPU path is present. The benchmark above measures the code as
shipped; results will vary by machine and input.

## How it works

The Python layer owns TOML namespace rules and creates ordinary Python dictionaries,
lists, strings, numbers, and `datetime` objects. At an eligible array it passes the
ASCII source to one Mojo call. Mojo scans comments and whitespace, validates numeric
grammar, decodes booleans and 64-bit integers, and returns the position after the closing
bracket plus typed value records.

The compatibility parser follows tomli's MIT-licensed grammar and namespace semantics;
its original copyright notice is retained in this repository's license.

The FFI is a flat C ABI. The NumPy source view remains zero-copy at the FFI boundary.
Buffers cross as integer addresses and are reconstructed as
`UnsafePointer[..., AnyOrigin[mut=True]]` inside Mojo. The caller owns every allocation:
one contiguous `uint8` source buffer and structure-of-arrays result buffers for `uint8`
type tags, `int64` values and source spans, and `float64` scratch values. Float source
spans are converted by Python's `float` to retain exactly the same rounding as tomli.
Unsupported arrays return to the Python parser without changing observable behavior.

MIT licensed.
