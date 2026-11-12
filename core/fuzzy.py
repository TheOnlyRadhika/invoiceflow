"""Fuzzy string matching. Uses rapidfuzz when installed, else a difflib fallback
with the same 0-100 scale (keeps tests runnable anywhere)."""
import re
from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz as _fuzz
except ImportError:          # pragma: no cover
    _fuzz = None

_STOP = {"pvt", "ltd", "private", "limited", "llp", "inc", "the", "and", "co", "per", "of", "for", "to", "-"}


def clean(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(t for t in s.split() if t not in _STOP)


def ratio(a: str, b: str) -> float:
    a, b = clean(a), clean(b)
    if _fuzz:
        return float(_fuzz.ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100


def token_set(a: str, b: str) -> float:
    """Order-insensitive similarity; robust to extra words on either side."""
    a, b = clean(a), clean(b)
    if _fuzz:
        return float(_fuzz.token_set_ratio(a, b))
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    ra = " ".join(sorted(ta - tb))
    rb = " ".join(sorted(tb - ta))
    s1 = (inter + " " + ra).strip()
    s2 = (inter + " " + rb).strip()
    cands = [SequenceMatcher(None, s1, s2).ratio()]
    if inter:
        cands += [SequenceMatcher(None, inter, s1).ratio(), SequenceMatcher(None, inter, s2).ratio()]
    return max(cands) * 100
