from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
import sys
from typing import Any, BinaryIO, Callable

import numpy as np

from ._ffi import parse_primitive_array

ParseFloat = Callable[[str], Any]
Key = tuple[str, ...]
MAX_INLINE_NESTING = sys.getrecursionlimit()
MAX_KEY_PARTS = sys.getrecursionlimit()
TOML_WS = " \t"
ARRAY_WS = " \t\n"
BARE_KEY = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
HEX = frozenset("abcdefABCDEF0123456789")
CTRL = frozenset(chr(i) for i in range(32)) | frozenset(chr(127))
ILLEGAL_BASIC = CTRL - frozenset("\t")
ILLEGAL_MULTILINE = CTRL - frozenset("\t\n")
ESCAPES = {
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    '"': '"',
    "\\": "\\",
}

TIME_RE = (
    r"([01][0-9]|2[0-3]):([0-5][0-9])"
    r"(?::([0-5][0-9])(?:\.([0-9]{1,6})[0-9]*)?)?"
)
RE_DATETIME = re.compile(
    r"([0-9]{4})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    rf"(?:[Tt ]{TIME_RE}"
    r"(?:(?:([Zz]))|(?:([+-])([01][0-9]|2[0-3]):([0-5][0-9])))?)?"
)
RE_LOCALTIME = re.compile(TIME_RE)
RE_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
RE_NUMBER = re.compile(
    r"(?:0(?:x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*|"
    r"b[01](?:_?[01])*|o[0-7](?:_?[0-7])*)|"
    r"[+-]?(?:0|[1-9](?:_?[0-9])*)"
    r"(?P<floatpart>(?:\.[0-9](?:_?[0-9])*)?"
    r"(?:[eE][+-]?[0-9](?:_?[0-9])*)?))"
)


class _DeprecatedDefault:
    pass


class TOMLDecodeError(ValueError):
    def __init__(
        self,
        msg: str | type[_DeprecatedDefault] = _DeprecatedDefault,
        doc: str | type[_DeprecatedDefault] = _DeprecatedDefault,
        pos: int | type[_DeprecatedDefault] = _DeprecatedDefault,
        *args: Any,
    ):
        if (
            args
            or not isinstance(msg, str)
            or not isinstance(doc, str)
            or not isinstance(pos, int)
        ):
            import warnings

            warnings.warn(
                "Free-form arguments for TOMLDecodeError are deprecated. "
                "Please set 'msg' (str), 'doc' (str) and 'pos' (int) arguments only.",
                DeprecationWarning,
                stacklevel=2,
            )
            extra = list(args)
            if pos is not _DeprecatedDefault:
                extra.insert(0, pos)
            if doc is not _DeprecatedDefault:
                extra.insert(0, doc)
            if msg is not _DeprecatedDefault:
                extra.insert(0, msg)
            super().__init__(*extra)
            return
        lineno = doc.count("\n", 0, pos) + 1
        colno = pos + 1 if lineno == 1 else pos - doc.rindex("\n", 0, pos)
        where = "end of document" if pos >= len(doc) else f"line {lineno}, column {colno}"
        super().__init__(f"{msg} (at {where})")
        self.msg = msg
        self.doc = doc
        self.pos = pos
        self.lineno = lineno
        self.colno = colno


class Flags:
    FROZEN = 0
    EXPLICIT_NEST = 1

    def __init__(self) -> None:
        self._flags: dict[str, dict[str, Any]] = {}
        self._pending: set[tuple[Key, int]] = set()

    def add_pending(self, key: Key, flag: int) -> None:
        self._pending.add((key, flag))

    def finalize_pending(self) -> None:
        for key, flag in self._pending:
            self.set(key, flag, recursive=False)
        self._pending.clear()

    def unset_all(self, key: Key) -> None:
        node = self._flags
        for part in key[:-1]:
            if part not in node:
                return
            node = node[part]["nested"]
        node.pop(key[-1], None)

    def set(self, key: Key, flag: int, *, recursive: bool) -> None:
        if not key:
            return
        node = self._flags
        for index, part in enumerate(key):
            node = node.setdefault(
                part, {"flags": set(), "recursive": set(), "nested": {}}
            )
            if index + 1 < len(key):
                node = node["nested"]
        node["recursive" if recursive else "flags"].add(flag)

    def is_(self, key: Key, flag: int) -> bool:
        if not key:
            return False
        node = self._flags
        for part in key:
            if part not in node:
                return False
            item = node[part]
            if flag in item["recursive"]:
                return True
            node = item["nested"]
        return flag in item["flags"]


class NestedDict:
    def __init__(self) -> None:
        self.dict: dict[str, Any] = {}

    def get_or_create(self, key: Key, *, access_lists: bool = True) -> dict[str, Any]:
        node: Any = self.dict
        for part in key:
            if part not in node:
                node[part] = {}
            node = node[part]
            if access_lists and isinstance(node, list):
                node = node[-1]
            if not isinstance(node, dict):
                raise KeyError
        return node

    def append_list(self, key: Key) -> None:
        node = self.get_or_create(key[:-1])
        stem = key[-1]
        if stem not in node:
            node[stem] = [{}]
        elif isinstance(node[stem], list):
            node[stem].append({})
        else:
            raise KeyError


class Parser:
    def __init__(self, source: str, parse_float: ParseFloat):
        self.source = source
        self.n = len(source)
        self.pos = 0
        self.parse_float = parse_float
        self.default_float = parse_float is float
        self.data = NestedDict()
        self.flags = Flags()
        self.header: Key = ()
        self.table = self.data.dict
        self.ascii_fast_path = source.isascii() and "\r" not in source
        self.source_bytes: np.ndarray[Any, np.dtype[np.uint8]] | None = None

    def error(self, message: str, pos: int | None = None) -> TOMLDecodeError:
        return TOMLDecodeError(message, self.source, self.pos if pos is None else pos)

    def skip_ws(self, *, newline: bool = False) -> None:
        allowed = ARRAY_WS if newline else TOML_WS
        while self.pos < self.n and self.source[self.pos] in allowed:
            self.pos += 1

    def skip_comment(self) -> None:
        if self.pos >= self.n or self.source[self.pos] != "#":
            return
        self.pos += 1
        while self.pos < self.n and self.source[self.pos] != "\n":
            if self.source[self.pos] in ILLEGAL_BASIC:
                raise self.error(
                    f"Found invalid character {self.source[self.pos]!r}"
                )
            self.pos += 1

    def skip_array_ws(self) -> None:
        while True:
            old = self.pos
            self.skip_ws(newline=True)
            if self.pos < self.n and self.source[self.pos] == "#":
                self.skip_comment()
            if old == self.pos:
                return

    def parse(self) -> dict[str, Any]:
        while True:
            self.skip_ws()
            if self.pos >= self.n:
                break
            char = self.source[self.pos]
            if char == "\n":
                self.pos += 1
                continue
            if char == "#":
                self.skip_comment()
            elif char == "[":
                self.flags.finalize_pending()
                self.parse_table()
                self.skip_ws()
            elif char in BARE_KEY or char in "\"'":
                self.parse_key_value(self.data, self.flags, self.header)
                self.skip_ws()
            else:
                raise self.error("Invalid statement")
            self.skip_comment()
            if self.pos >= self.n:
                break
            if self.source[self.pos] != "\n":
                raise self.error(
                    "Expected newline or end of document after a statement"
                )
            self.pos += 1
        return self.data.dict

    def parse_table(self) -> None:
        array = self.source.startswith("[[", self.pos)
        self.pos += 2 if array else 1
        self.skip_ws()
        key = self.parse_key()
        if array:
            if self.flags.is_(key, Flags.FROZEN):
                raise self.error(f"Cannot mutate immutable namespace {key}")
            self.flags.unset_all(key)
            self.flags.set(key, Flags.EXPLICIT_NEST, recursive=False)
            try:
                self.data.append_list(key)
            except KeyError:
                raise self.error("Cannot overwrite a value") from None
            self.table = self.data.get_or_create(key)
            if not self.source.startswith("]]", self.pos):
                raise self.error("Expected ']]' at the end of an array declaration")
            self.pos += 2
        else:
            if self.flags.is_(key, Flags.EXPLICIT_NEST) or self.flags.is_(
                key, Flags.FROZEN
            ):
                raise self.error(f"Cannot declare {key} twice")
            self.flags.set(key, Flags.EXPLICIT_NEST, recursive=False)
            try:
                self.table = self.data.get_or_create(key)
            except KeyError:
                raise self.error("Cannot overwrite a value") from None
            if self.pos >= self.n or self.source[self.pos] != "]":
                raise self.error("Expected ']' at the end of a table declaration")
            self.pos += 1
        self.header = key

    def parse_key_value(
        self, target: NestedDict, flags: Flags, header: Key
    ) -> None:
        key = self.parse_key()
        if self.pos >= self.n or self.source[self.pos] != "=":
            raise self.error("Expected '=' after a key in a key/value pair")
        self.pos += 1
        self.skip_ws()
        value = self.parse_value(0)
        if target is self.data and header == self.header and len(key) == 1:
            stem = key[0]
            if stem in self.table:
                raise self.error("Cannot overwrite a value")
            if isinstance(value, (dict, list)):
                flags.set(header + key, Flags.FROZEN, recursive=True)
            self.table[stem] = value
            return
        parent, stem = key[:-1], key[-1]
        absolute_parent = header + parent
        for index in range(1, len(key)):
            container = header + key[:index]
            if flags.is_(container, Flags.EXPLICIT_NEST):
                raise self.error(f"Cannot redefine namespace {container}")
            flags.add_pending(container, Flags.EXPLICIT_NEST)
        if flags.is_(absolute_parent, Flags.FROZEN):
            raise self.error(f"Cannot mutate immutable namespace {absolute_parent}")
        try:
            node = target.get_or_create(absolute_parent)
        except KeyError:
            raise self.error("Cannot overwrite a value") from None
        if stem in node:
            raise self.error("Cannot overwrite a value")
        if isinstance(value, (dict, list)):
            flags.set(header + key, Flags.FROZEN, recursive=True)
        node[stem] = value

    def parse_key(self) -> Key:
        first = self.parse_key_part()
        self.skip_ws()
        if self.pos >= self.n or self.source[self.pos] != ".":
            return (first,)
        parts = [first]
        while self.pos < self.n and self.source[self.pos] == ".":
            self.pos += 1
            self.skip_ws()
            parts.append(self.parse_key_part())
            if len(parts) > MAX_KEY_PARTS:
                raise RecursionError(
                    f"TOML key has more than the allowed {MAX_KEY_PARTS} parts"
                )
            self.skip_ws()
        return tuple(parts)

    def parse_key_part(self) -> str:
        if self.pos >= self.n:
            raise self.error("Invalid initial character for a key part")
        char = self.source[self.pos]
        if char in BARE_KEY:
            match = RE_BARE_KEY.match(self.source, self.pos)
            self.pos = match.end()
            return match.group()
        if char == "'":
            return self.parse_literal_string(multiline=False)
        if char == '"':
            return self.parse_basic_string(multiline=False)
        raise self.error("Invalid initial character for a key part")

    def parse_value(self, level: int) -> Any:
        if level > MAX_INLINE_NESTING:
            raise RecursionError(
                "TOML inline arrays/tables are nested more than the allowed "
                f"{MAX_INLINE_NESTING} levels"
            )
        if self.pos >= self.n:
            raise self.error("Invalid value")
        char = self.source[self.pos]
        if char == '"':
            if self.source.startswith('"""', self.pos):
                return self.parse_basic_string(multiline=True)
            return self.parse_basic_string(multiline=False)
        if char == "'":
            if self.source.startswith("'''", self.pos):
                return self.parse_literal_string(multiline=True)
            return self.parse_literal_string(multiline=False)
        if char == "t" and self.source.startswith("true", self.pos):
            self.pos += 4
            return True
        if char == "f" and self.source.startswith("false", self.pos):
            self.pos += 5
            return False
        if char == "[":
            return self.parse_array(level + 1)
        if char == "{":
            return self.parse_inline_table(level + 1)
        if "0" <= char <= "9":
            if (
                self.pos + 4 < self.n
                and self.source[self.pos + 4] == "-"
            ):
                match = RE_DATETIME.match(self.source, self.pos)
                if match:
                    try:
                        value = self.datetime_from_match(match)
                    except ValueError as exc:
                        raise self.error("Invalid date or datetime") from exc
                    self.pos = match.end()
                    return value
            if (
                self.pos + 2 < self.n
                and self.source[self.pos + 2] == ":"
            ):
                match = RE_LOCALTIME.match(self.source, self.pos)
                if match:
                    self.pos = match.end()
                    return self.time_from_groups(match.groups())
        if "0" <= char <= "9" or char in "+-":
            match = RE_NUMBER.match(self.source, self.pos)
            if match:
                self.pos = match.end()
                token = match.group()
                if match.group("floatpart"):
                    return self.parse_float(token)
                return int(token, 0)
        for size in (3, 4):
            token = self.source[self.pos : self.pos + size]
            if token in {"inf", "nan", "-inf", "+inf", "-nan", "+nan"}:
                self.pos += size
                return self.parse_float(token)
        raise self.error("Invalid value")

    def parse_array(self, level: int) -> list[Any]:
        if self.ascii_fast_path and self.default_float:
            if self.source_bytes is None:
                self.source_bytes = np.frombuffer(
                    self.source.encode("ascii"), dtype=np.uint8
                )
            result = parse_primitive_array(self.source_bytes, self.source, self.pos)
            if result is not None:
                self.pos, values = result
                return values
        self.pos += 1
        values: list[Any] = []
        self.skip_array_ws()
        if self.pos < self.n and self.source[self.pos] == "]":
            self.pos += 1
            return values
        while True:
            values.append(self.parse_value(level))
            self.skip_array_ws()
            if self.pos < self.n and self.source[self.pos] == "]":
                self.pos += 1
                return values
            if self.pos >= self.n or self.source[self.pos] != ",":
                raise self.error("Unclosed array")
            self.pos += 1
            self.skip_array_ws()
            if self.pos < self.n and self.source[self.pos] == "]":
                self.pos += 1
                return values

    def parse_inline_table(self, level: int) -> dict[str, Any]:
        self.pos += 1
        target = NestedDict()
        flags = Flags()
        self.skip_array_ws()
        if self.pos < self.n and self.source[self.pos] == "}":
            self.pos += 1
            return target.dict
        while True:
            self.parse_key_value(target, flags, ())
            self.skip_array_ws()
            if self.pos < self.n and self.source[self.pos] == "}":
                self.pos += 1
                return target.dict
            if self.pos >= self.n or self.source[self.pos] != ",":
                raise self.error("Unclosed inline table")
            self.pos += 1
            self.skip_array_ws()
            if self.pos < self.n and self.source[self.pos] == "}":
                self.pos += 1
                return target.dict

    def parse_literal_string(self, *, multiline: bool) -> str:
        delimiter = "'''" if multiline else "'"
        self.pos += len(delimiter)
        if multiline and self.pos < self.n and self.source[self.pos] == "\n":
            self.pos += 1
        start = self.pos
        end = self.source.find(delimiter, self.pos)
        if end < 0:
            self.pos = self.n
            raise self.error(f"Expected {delimiter!r}")
        illegal = ILLEGAL_MULTILINE if multiline else ILLEGAL_BASIC
        value = self.source[start:end]
        if value.isprintable() or value.replace("\t", "").isprintable():
            self.pos = end + len(delimiter)
            if multiline:
                extras = 0
                while (
                    extras < 2
                    and self.pos < self.n
                    and self.source[self.pos] == "'"
                ):
                    value += "'"
                    self.pos += 1
                    extras += 1
            return value
        for index in range(start, end):
            if self.source[index] in illegal:
                raise self.error(
                    f"Found invalid character {self.source[index]!r}", index
                )
        self.pos = end + len(delimiter)
        if multiline:
            extras = 0
            while extras < 2 and self.pos < self.n and self.source[self.pos] == "'":
                value += "'"
                self.pos += 1
                extras += 1
        return value

    def parse_basic_string(self, *, multiline: bool) -> str:
        delimiter = '"""' if multiline else '"'
        self.pos += len(delimiter)
        if multiline and self.pos < self.n and self.source[self.pos] == "\n":
            self.pos += 1
        if not multiline:
            end = self.source.find('"', self.pos)
            if end >= 0 and self.source.find("\\", self.pos, end) < 0:
                value = self.source[self.pos : end]
                if value.isprintable() or value.replace("\t", "").isprintable():
                    self.pos = end + 1
                    return value
        pieces: list[str] = []
        start = self.pos
        illegal = ILLEGAL_MULTILINE if multiline else ILLEGAL_BASIC
        while self.pos < self.n:
            if multiline and self.source.startswith('"""', self.pos):
                pieces.append(self.source[start : self.pos])
                self.pos += 3
                extras = 0
                while (
                    extras < 2
                    and self.pos < self.n
                    and self.source[self.pos] == '"'
                ):
                    pieces.append('"')
                    self.pos += 1
                    extras += 1
                return "".join(pieces)
            char = self.source[self.pos]
            if not multiline and char == '"':
                pieces.append(self.source[start : self.pos])
                self.pos += 1
                return "".join(pieces)
            if char == "\\":
                pieces.append(self.source[start : self.pos])
                self.pos += 1
                if multiline and self.consume_multiline_continuation():
                    start = self.pos
                    continue
                pieces.append(self.parse_escape())
                start = self.pos
                continue
            if char in illegal:
                raise self.error(f"Illegal character {char!r}")
            self.pos += 1
        raise self.error("Unterminated string")

    def consume_multiline_continuation(self) -> bool:
        probe = self.pos
        while probe < self.n and self.source[probe] in TOML_WS:
            probe += 1
        if probe >= self.n or self.source[probe] != "\n":
            return False
        probe += 1
        while probe < self.n and self.source[probe] in ARRAY_WS:
            probe += 1
        self.pos = probe
        return True

    def parse_escape(self) -> str:
        if self.pos >= self.n:
            raise self.error("Unescaped '\\' in a string")
        escape = self.source[self.pos]
        self.pos += 1
        if escape in ESCAPES:
            return ESCAPES[escape]
        lengths = {"x": 2, "u": 4, "U": 8}
        if escape not in lengths:
            raise self.error("Unescaped '\\' in a string")
        size = lengths[escape]
        digits = self.source[self.pos : self.pos + size]
        if len(digits) != size or not HEX.issuperset(digits):
            raise self.error("Invalid hex value")
        self.pos += size
        codepoint = int(digits, 16)
        if not (0 <= codepoint <= 0xD7FF or 0xE000 <= codepoint <= 0x10FFFF):
            raise self.error("Escaped character is not a Unicode scalar value")
        return chr(codepoint)

    @staticmethod
    def time_from_groups(groups: tuple[str | None, ...]) -> time:
        hour, minute, second, fraction = groups[:4]
        micros = int((fraction or "").ljust(6, "0")) if fraction else 0
        return time(int(hour), int(minute), int(second or 0), micros)

    @classmethod
    def datetime_from_match(cls, match: re.Match[str]) -> date | datetime:
        groups = match.groups()
        year, month, day = map(int, groups[:3])
        if groups[3] is None:
            return date(year, month, day)
        parsed_time = cls.time_from_groups(groups[3:7])
        zulu, sign, offset_hour, offset_minute = groups[7:11]
        tz = None
        if zulu:
            tz = timezone.utc
        elif sign:
            direction = 1 if sign == "+" else -1
            tz = timezone(
                timedelta(
                    hours=direction * int(offset_hour),
                    minutes=direction * int(offset_minute),
                )
            )
        return datetime.combine(date(year, month, day), parsed_time, tzinfo=tz)


def make_safe_parse_float(parse_float: ParseFloat) -> ParseFloat:
    if parse_float is float:
        return float

    def safe(value: str) -> Any:
        parsed = parse_float(value)
        if isinstance(parsed, (dict, list)):
            raise ValueError("parse_float must not return dicts or lists")
        return parsed

    return safe


def loads(__s: str, *, parse_float: ParseFloat = float) -> dict[str, Any]:
    try:
        source = __s.replace("\r\n", "\n")
    except (AttributeError, TypeError):
        raise TypeError(f"Expected str object, not '{type(__s).__qualname__}'") from None
    return Parser(source, make_safe_parse_float(parse_float)).parse()


def load(__fp: BinaryIO, *, parse_float: ParseFloat = float) -> dict[str, Any]:
    data = __fp.read()
    try:
        source = data.decode()
    except AttributeError:
        raise TypeError(
            "File must be opened in binary mode, e.g. use `open('foo.toml', 'rb')`"
        ) from None
    return loads(source, parse_float=parse_float)
