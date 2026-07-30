import math
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))


def assert_same(left, right):
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return
        assert left == right
        return
    if isinstance(left, Decimal) and isinstance(right, Decimal):
        if left.is_nan() and right.is_nan():
            return
    assert type(left) is type(right)
    if isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            assert_same(left[key], right[key])
    elif isinstance(left, list):
        assert len(left) == len(right)
        for left_item, right_item in zip(left, right, strict=True):
            assert_same(left_item, right_item)
    else:
        assert left == right
