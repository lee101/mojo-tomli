"""Primitive TOML array decoder exposed through a small C ABI."""

comptime BytePtr = UnsafePointer[UInt8, AnyOrigin[mut=True]]
comptime I64Ptr = UnsafePointer[Int64, AnyOrigin[mut=True]]


def byte(src: BytePtr, i: Int) -> Int:
    return Int(src.load(i))


def is_digit(c: Int) -> Bool:
    return c >= 48 and c <= 57


def digit_for_base(c: Int, base: Int) -> Int:
    if c >= 48 and c <= 57:
        var d = c - 48
        return d if d < base else -1
    if c >= 65 and c <= 70:
        var d = c - 65 + 10
        return d if d < base else -1
    if c >= 97 and c <= 102:
        var d = c - 97 + 10
        return d if d < base else -1
    return -1


def is_value_end(c: Int) -> Bool:
    return c == 32 or c == 9 or c == 10 or c == 13 or c == 44 or c == 93 or c == 35


def skip_array_ws(src: BytePtr, n: Int, pos_in: Int) -> Int:
    var pos = pos_in
    while pos < n:
        var c = byte(src, pos)
        if c == 32 or c == 9 or c == 10 or c == 13:
            pos += 1
            continue
        if c == 35:
            pos += 1
            while pos < n and byte(src, pos) != 10:
                pos += 1
            continue
        break
    return pos


def pow10(exp_in: Int) -> Float64:
    var exp = exp_in
    var base = 10.0
    var value = 1.0
    if exp < 0:
        exp = -exp
        base = 0.1
    while exp > 0:
        if (exp & 1) != 0:
            value *= base
        base *= base
        exp >>= 1
    return value


def parse_based_int(
    src: BytePtr, start: Int, end: Int, sign: Int, base: Int, digits_start: Int
) -> Tuple[Bool, Int64]:
    if digits_start >= end:
        return (False, Int64(0))
    var value = UInt64(0)
    var prev_digit = False
    var digits = 0
    var pos = digits_start
    while pos < end:
        var c = byte(src, pos)
        if c == 95:
            if not prev_digit or pos + 1 >= end:
                return (False, Int64(0))
            prev_digit = False
            pos += 1
            continue
        var d = digit_for_base(c, base)
        if d < 0:
            return (False, Int64(0))
        var limit = UInt64(9223372036854775808) if sign < 0 else UInt64(9223372036854775807)
        if value > (limit - UInt64(d)) // UInt64(base):
            return (False, Int64(0))
        value = value * UInt64(base) + UInt64(d)
        prev_digit = True
        digits += 1
        pos += 1
    if not prev_digit or digits == 0:
        return (False, Int64(0))
    if sign < 0:
        if value == UInt64(9223372036854775808):
            return (True, Int64(-9223372036854775807) - Int64(1))
        return (True, -Int64(value))
    return (True, Int64(value))


def parse_number(
    src: BytePtr, start: Int, end: Int
) -> Tuple[Int, Int64, Float64]:
    var pos = start
    var sign = 1
    if pos < end and byte(src, pos) == 43:
        pos += 1
    elif pos < end and byte(src, pos) == 45:
        sign = -1
        pos += 1
    if pos >= end:
        return (0, Int64(0), 0.0)

    if end - pos == 3:
        var c0 = byte(src, pos)
        var c1 = byte(src, pos + 1)
        var c2 = byte(src, pos + 2)
        if c0 == 105 and c1 == 110 and c2 == 102:
            var zero = 0.0
            var infinity = 1.0 / zero
            return (2, Int64(0), infinity if sign > 0 else -infinity)
        if c0 == 110 and c1 == 97 and c2 == 110:
            var zero = 0.0
            return (2, Int64(0), zero / zero)

    if end - pos >= 2 and byte(src, pos) == 48:
        var prefix = byte(src, pos + 1)
        if prefix == 120:
            if pos != start:
                return (0, Int64(0), 0.0)
            var parsed = parse_based_int(src, start, end, sign, 16, pos + 2)
            return (1, parsed[1], 0.0) if parsed[0] else (0, Int64(0), 0.0)
        if prefix == 111:
            if pos != start:
                return (0, Int64(0), 0.0)
            var parsed = parse_based_int(src, start, end, sign, 8, pos + 2)
            return (1, parsed[1], 0.0) if parsed[0] else (0, Int64(0), 0.0)
        if prefix == 98:
            if pos != start:
                return (0, Int64(0), 0.0)
            var parsed = parse_based_int(src, start, end, sign, 2, pos + 2)
            return (1, parsed[1], 0.0) if parsed[0] else (0, Int64(0), 0.0)

    var whole = 0.0
    var digits = 0
    var prev_digit = False
    var leading_zero = False
    if pos < end and byte(src, pos) == 48:
        leading_zero = True
    while pos < end:
        var c = byte(src, pos)
        if is_digit(c):
            if leading_zero and digits > 0:
                return (0, Int64(0), 0.0)
            whole = whole * 10.0 + Float64(c - 48)
            digits += 1
            prev_digit = True
            pos += 1
        elif c == 95:
            if not prev_digit or pos + 1 >= end or not is_digit(byte(src, pos + 1)):
                return (0, Int64(0), 0.0)
            prev_digit = False
            pos += 1
        else:
            break
    if digits == 0 or not prev_digit:
        return (0, Int64(0), 0.0)

    var is_float = False
    var fraction = 0.0
    var scale = 1.0
    if pos < end and byte(src, pos) == 46:
        is_float = True
        pos += 1
        var fraction_digits = 0
        prev_digit = False
        while pos < end:
            var c = byte(src, pos)
            if is_digit(c):
                scale *= 0.1
                fraction += Float64(c - 48) * scale
                fraction_digits += 1
                prev_digit = True
                pos += 1
            elif c == 95:
                if not prev_digit or pos + 1 >= end or not is_digit(byte(src, pos + 1)):
                    return (0, Int64(0), 0.0)
                prev_digit = False
                pos += 1
            else:
                break
        if fraction_digits == 0 or not prev_digit:
            return (0, Int64(0), 0.0)

    var exponent = 0
    var exponent_sign = 1
    if pos < end and (byte(src, pos) == 101 or byte(src, pos) == 69):
        is_float = True
        pos += 1
        if pos < end and byte(src, pos) == 43:
            pos += 1
        elif pos < end and byte(src, pos) == 45:
            exponent_sign = -1
            pos += 1
        var exponent_digits = 0
        prev_digit = False
        while pos < end:
            var c = byte(src, pos)
            if is_digit(c):
                if exponent < 10000:
                    exponent = exponent * 10 + c - 48
                exponent_digits += 1
                prev_digit = True
                pos += 1
            elif c == 95:
                if not prev_digit or pos + 1 >= end or not is_digit(byte(src, pos + 1)):
                    return (0, Int64(0), 0.0)
                prev_digit = False
                pos += 1
            else:
                break
        if exponent_digits == 0 or not prev_digit:
            return (0, Int64(0), 0.0)

    if pos != end:
        return (0, Int64(0), 0.0)
    if is_float:
        var value = Float64(sign) * (whole + fraction) * pow10(exponent_sign * exponent)
        return (2, Int64(0), value)
    var digits_start = start
    if byte(src, start) == 43 or byte(src, start) == 45:
        digits_start += 1
    var parsed_int = parse_based_int(src, start, end, sign, 10, digits_start)
    if parsed_int[0]:
        return (1, parsed_int[1], 0.0)
    return (0, Int64(0), 0.0)


@export("mt_parse_primitive_array")
def mt_parse_primitive_array(
    src_addr: Int,
    n: Int,
    start: Int,
    kinds_addr: Int,
    ints_addr: Int,
    starts_addr: Int,
    ends_addr: Int,
    capacity: Int,
) abi("C") -> Int:
    # Validate every scalar before constructing or dereferencing a non-nullable
    # pointer. Python owns and keeps all five contiguous buffers alive for the
    # duration of this call.
    if src_addr == 0 or kinds_addr == 0 or ints_addr == 0 \
        or starts_addr == 0 or ends_addr == 0:
        return -3
    if n <= 0 or start < 0 or start >= n or capacity <= 0:
        return -3
    var src = BytePtr(unsafe_from_address=src_addr)
    var kinds = BytePtr(unsafe_from_address=kinds_addr)
    var ints = I64Ptr(unsafe_from_address=ints_addr)
    var starts = I64Ptr(unsafe_from_address=starts_addr)
    var ends = I64Ptr(unsafe_from_address=ends_addr)
    if byte(src, start) != 91:
        return -1
    var pos = skip_array_ws(src, n, start + 1)
    var count = 0
    if pos < n and byte(src, pos) == 93:
        ints.store(capacity, Int64(0))
        return pos + 1
    while pos < n:
        if count >= capacity:
            return -2
        var token_start = pos
        while pos < n and not is_value_end(byte(src, pos)):
            pos += 1
        if token_start == pos:
            return -1
        var token_end = pos
        var token_len = token_end - token_start
        starts.store(count, Int64(token_start))
        ends.store(count, Int64(token_end))
        if token_len == 4 and byte(src, token_start) == 116 and byte(src, token_start + 1) == 114 \
            and byte(src, token_start + 2) == 117 and byte(src, token_start + 3) == 101:
            kinds.store(count, UInt8(3))
            ints.store(count, Int64(1))
        elif token_len == 5 and byte(src, token_start) == 102 and byte(src, token_start + 1) == 97 \
            and byte(src, token_start + 2) == 108 and byte(src, token_start + 3) == 115 \
            and byte(src, token_start + 4) == 101:
            kinds.store(count, UInt8(3))
            ints.store(count, Int64(0))
        else:
            var parsed = parse_number(src, token_start, token_end)
            if parsed[0] == 0:
                return -1
            kinds.store(count, UInt8(parsed[0]))
            ints.store(count, parsed[1])
        count += 1
        pos = skip_array_ws(src, n, pos)
        if pos >= n:
            return -1
        if byte(src, pos) == 93:
            ints.store(capacity, Int64(count))
            return pos + 1
        if byte(src, pos) != 44:
            return -1
        pos = skip_array_ws(src, n, pos + 1)
        if pos < n and byte(src, pos) == 93:
            ints.store(capacity, Int64(count))
            return pos + 1
    return -1
