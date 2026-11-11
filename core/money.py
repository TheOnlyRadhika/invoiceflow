"""Money helpers. All amounts are Decimal, rounded to 2 places (Golden rule 5)."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TWO = Decimal("0.01")


def to_decimal(value) -> Decimal | None:
    """Parse '₹4,13,000.00', 'Rs. 4,13,000', 413000, '413000.5' -> Decimal. None if unparseable."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(TWO, ROUND_HALF_UP)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(TWO, ROUND_HALF_UP)
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"(?i)(inr|rs\.?|₹)", "", s)
    s = s.replace(",", "").replace(" ", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return (-d if negative else d).quantize(TWO, ROUND_HALF_UP)


def format_inr(value, symbol: bool = True) -> str:
    """Indian digit grouping: 413000 -> '₹4,13,000.00'."""
    d = to_decimal(value)
    if d is None:
        return "—"
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{'₹' if symbol else ''}{whole}.{frac}"


def pct_diff(actual, expected) -> Decimal:
    """Percent by which actual exceeds expected (signed). 0 if expected is 0/None."""
    a, e = to_decimal(actual), to_decimal(expected)
    if a is None or e is None or e == 0:
        return Decimal("0")
    return ((a - e) / e * 100).quantize(TWO, ROUND_HALF_UP)


def close_enough(a, b, tol) -> bool:
    a, b = to_decimal(a), to_decimal(b)
    if a is None or b is None:
        return False
    return abs(a - b) <= tol
