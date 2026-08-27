from datetime import date, datetime, time, timezone
from decimal import Decimal
import io

import pytest
import tomli
import numpy as np

import mojo_tomli
from mojo_tomli import _ffi, _parser
from conftest import assert_same


@pytest.mark.parametrize(
    "document",
    [
        "",
        "# comment only\n",
        "zero = 0\nnegative = -17\npositive = +99\n",
        "hex = 0xDEAD_BEEF\noct = 0o755\nbin = 0b1101_0010\n",
        "small = 1.25\nlarge = -5e+22\ntiny = 6.626e-34\n",
        "yes = true\nno = false\np = inf\nn = -inf\nnan = nan\n",
        "basic = \"tab\\tline\\nquote: \\\"\"\nliteral = 'C:\\\\Users\\\\node'\n",
        'unicode = "Greek alpha: α and escaped: \\u03B1"\n',
        'byte_escapes = "\\x41\\e"\n',
        "date = 1979-05-27\ntime = 07:32:00.123456789\n",
        "local = 1979-05-27T07:32:00\nutc = 1979-05-27T07:32:00Z\n",
        "offset = 1979-05-27 07:32:00-08:30\n",
        "empty = []\nints = [1, 2, 3,]\nbools = [true, false]\n",
        "numbers = [0x10, 0o10, 0b10, -2, 3.5, 4e2, inf, nan]\n",
        "nested = [[1, 2], [3, 4]]\n",
        "with_comments = [\n  1, # first\n  2,\n  3,\n]\n",
        'point = { x = 1, y = 2, label = "origin" }\n',
        "dotted.key.value = 3\n",
        '"quoted.key"."with space" = "ok"\n',
        "[owner]\nname = \"Tom\"\nactive = true\n",
        "[database.settings]\nports = [8000, 8001, 8002]\n",
        "[[products]]\nname = \"Hammer\"\n[[products]]\nname = \"Nail\"\n",
        "[[fruits]]\nname = \"apple\"\n[fruits.physical]\ncolor = \"red\"\n",
        'multiline = """\nThe quick brown \\\n  fox jumps.\n"""\n',
        "literal = '''\nfirst\nsecond ' line\n'''\n",
    ],
)
def test_loads_matches_tomli(document):
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_reference_example_matches_tomli():
    document = """
title = "TOML Example"

[owner]
name = "Tom Preston-Werner"
dob = 1979-05-27T07:32:00-08:00

[database]
enabled = true
ports = [8000, 8001, 8002]
data = [["delta", "phi"], [3.14]]
temp_targets = {cpu = 79.5, case = 72.0}

[[servers]]
ip = "10.0.0.1"
role = "frontend"

[[servers]]
ip = "10.0.0.2"
role = "backend"
"""
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_large_primitive_arrays_match_tomli():
    integers = ", ".join(str(index - 20_000) for index in range(40_000))
    floats = ", ".join(f"{index}.125e-2" for index in range(20_000))
    document = f"integers = [{integers}]\nfloats = [{floats}]\n"
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_primitive_array_capacity_ignores_comment_punctuation():
    document = "values = [1, # misleading ],,,\n2, 3]\n"
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_simple_key_and_string_fast_paths_match_tomli():
    document = (
        '[service]\n'
        'plain_key = "printable value"\n'
        'literal = \'literal value\'\n'
        'tab = "before\tafter"\n'
        'escaped = "line\\nquote: \\""\n'
        'integer = -17\n'
        'ratio = 0.25\n'
    )
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_flat_scalar_document_fast_path_matches(monkeypatch):
    document = (
        'name = "demo"\n'
        "enabled = true\n"
        "retries = -3\n"
        "ratio = 0.25\n"
        "limit = +inf\n"
    )
    expected = tomli.loads(document)

    class UnexpectedParser:
        def __init__(self, *args, **kwargs):
            raise AssertionError("flat scalar document used the semantic parser")

    monkeypatch.setattr(_parser, "Parser", UnexpectedParser)
    assert_same(mojo_tomli.loads(document), expected)


def test_consecutive_simple_string_lines_match_tomli():
    document = (
        "[service]\n"
        'first = "one"\n'
        'second = "two"\n'
        'third = "three"\n'
        "enabled = true\n"
    )
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_array_source_buffer_is_lazy_and_reused(monkeypatch):
    calls = 0
    frombuffer = _parser.np.frombuffer

    def recording_frombuffer(*args, **kwargs):
        nonlocal calls
        calls += 1
        return frombuffer(*args, **kwargs)

    monkeypatch.setattr(_parser.np, "frombuffer", recording_frombuffer)
    mojo_tomli.loads('name = "no arrays"\n')
    assert calls == 0
    document = "first = [1, 2, 3]\nsecond = [4, 5, 6]\n"
    assert_same(mojo_tomli.loads(document), tomli.loads(document))
    assert calls == 1


def test_integer_boundaries_match_tomli():
    document = "values = [-9223372036854775808, 9223372036854775807]\n"
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_non_ascii_before_accelerated_array_matches():
    document = 'name = "München"\nvalues = [1, 2, 3]\n'
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_parse_float_decimal_matches():
    document = "single = 0.1\nvalues = [1.5, 2e-2, nan]\n"
    assert_same(
        mojo_tomli.loads(document, parse_float=Decimal),
        tomli.loads(document, parse_float=Decimal),
    )


def test_parse_float_rejects_containers():
    with pytest.raises(ValueError, match="parse_float"):
        mojo_tomli.loads("x = 1.5", parse_float=lambda _: [])


def test_load_binary_matches():
    document = b"[tool]\nname = \"demo\"\nvalues = [1, 2, 3]\n"
    assert_same(
        mojo_tomli.load(io.BytesIO(document)),
        tomli.load(io.BytesIO(document)),
    )


def test_load_rejects_text_file():
    with pytest.raises(TypeError, match="binary mode"):
        mojo_tomli.load(io.StringIO("x = 1"))


def test_public_datetime_types():
    parsed = mojo_tomli.loads(
        "d = 2024-02-29\nt = 23:59:59.1\ndt = 2024-02-29T23:59:59Z\n"
    )
    assert parsed["d"] == date(2024, 2, 29)
    assert parsed["t"] == time(23, 59, 59, 100_000)
    assert parsed["dt"] == datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "document",
    [
        "key",
        "= 1",
        "key =",
        "key = 01",
        "key = 1_",
        "key = .5",
        "key = true false",
        "key = [1 2]",
        "key = [1,, 2]",
        "key = [1, 2",
        "key = {a = 1 a = 2}",
        "key = {a = 1, a = 2}",
        "key = \"unterminated",
        "key = \"bad\\q\"",
        "key = \"bad\\uD800\"",
        "[table\nx = 1",
        "[table]\n[table]\n",
        "x = 1\nx = 2\n",
        "x = 1\n[x]\ny = 2\n",
        "a.b = 1\n[a]\nc = 2\n",
        "when = 2023-02-29",
        "bad = 25:00:00",
        "x = [1,\r2]",
    ],
)
def test_invalid_documents_rejected_like_tomli(document):
    with pytest.raises(tomli.TOMLDecodeError):
        tomli.loads(document)
    with pytest.raises(mojo_tomli.TOMLDecodeError):
        mojo_tomli.loads(document)


def test_error_coordinates_match_upstream():
    document = "good = 1\nbad = [1, 2\n"
    with pytest.raises(tomli.TOMLDecodeError) as reference:
        tomli.loads(document)
    with pytest.raises(mojo_tomli.TOMLDecodeError) as ours:
        mojo_tomli.loads(document)
    assert ours.value.lineno == reference.value.lineno
    assert ours.value.colno == reference.value.colno
    assert ours.value.pos == reference.value.pos


def test_arbitrary_precision_integer_falls_back_and_matches():
    document = "x = [9223372036854775808, -9223372036854775809]"
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_loads_rejects_non_string_like_upstream():
    for value in (b"x = 1", None, 123):
        with pytest.raises(TypeError):
            tomli.loads(value)
        with pytest.raises(TypeError):
            mojo_tomli.loads(value)


def test_documented_key_and_inline_table_variants_match():
    document = """
"" = "empty"
'literal key' = 1
a.b.c = 2
multiline = {
  plain = true,
  nested = { value = 3 },
}
heterogeneous = [1, "two", 1979-05-27, false]
"""
    assert_same(mojo_tomli.loads(document), tomli.loads(document))


def test_decode_error_public_attributes_match_upstream():
    document = "valid = 1\ninvalid = ["
    with pytest.raises(tomli.TOMLDecodeError) as reference:
        tomli.loads(document)
    with pytest.raises(mojo_tomli.TOMLDecodeError) as ours:
        mojo_tomli.loads(document)
    for attribute in ("msg", "doc", "pos", "lineno", "colno"):
        assert getattr(ours.value, attribute) == getattr(reference.value, attribute)


@pytest.mark.parametrize(
    "array",
    [
        np.array([91, 93], dtype=np.int16),
        np.array([[91, 93]], dtype=np.uint8),
        np.array([91, 0, 93, 0], dtype=np.uint8)[::2],
    ],
)
def test_ffi_rejects_unsafe_source_layouts(array):
    with pytest.raises(TypeError):
        _ffi.parse_primitive_array(array, "[]", 0)


def test_ffi_rejects_source_length_and_start_mismatch():
    array = np.frombuffer(b"[]", dtype=np.uint8)
    with pytest.raises(ValueError):
        _ffi.parse_primitive_array(array, "[ ]", 0)
    with pytest.raises(ValueError):
        _ffi.parse_primitive_array(array, "[]", 2)
