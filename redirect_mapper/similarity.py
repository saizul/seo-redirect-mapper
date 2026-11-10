"""String similarity with an optional rapidfuzz backend and a difflib fallback."""
from __future__ import annotations

import difflib
import heapq
from typing import List, Sequence, Tuple

try:  # pragma: no cover - exercised by whichever environment runs the tests
    from rapidfuzz import fuzz as _fuzz
    from rapidfuzz import process as _process

    HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _fuzz = None
    _process = None
    HAS_RAPIDFUZZ = False

_use_rapidfuzz = HAS_RAPIDFUZZ


def backend() -> str:
    """Name of the active backend: ``rapidfuzz`` or ``difflib``."""
    return "rapidfuzz" if _use_rapidfuzz else "difflib"


def set_backend(name: str) -> None:
    """Force a backend (used by tests and the ``--no-rapidfuzz`` flag)."""
    global _use_rapidfuzz
    if name == "rapidfuzz":
        if not HAS_RAPIDFUZZ:
            raise RuntimeError("rapidfuzz is not installed")
        _use_rapidfuzz = True
    elif name == "difflib":
        _use_rapidfuzz = False
    else:
        raise ValueError("backend must be 'rapidfuzz' or 'difflib'")


def ratio(a: str, b: str) -> float:
    """Similarity of two strings in the range 0.0 - 1.0."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if _use_rapidfuzz:
        return float(_fuzz.ratio(a, b)) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def token_overlap(a: Sequence[str], b: Sequence[str]) -> float:
    """Jaccard similarity of two token collections."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def best_matches(
    query: str, choices: Sequence[str], limit: int = 3, cutoff: float = 0.0
) -> List[Tuple[str, float, int]]:
    """Return up to *limit* ``(choice, score, index)`` tuples, best first."""
    if not query or not choices:
        return []
    if _use_rapidfuzz:
        found = _process.extract(
            query, choices, scorer=_fuzz.ratio, limit=limit, score_cutoff=cutoff * 100
        )
        return [(c, float(s) / 100.0, int(i)) for c, s, i in found]
    scored = []
    matcher = difflib.SequenceMatcher()
    matcher.set_seq2(query)
    for idx, choice in enumerate(choices):
        matcher.set_seq1(choice)
        if matcher.real_quick_ratio() < cutoff or matcher.quick_ratio() < cutoff:
            continue
        score = matcher.ratio()
        if score >= cutoff:
            scored.append((score, -idx, choice))
    top = heapq.nlargest(limit, scored)
    return [(choice, score, -neg) for score, neg, choice in top]
