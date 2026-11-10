"""The matching pipeline: old URL -> best new URL with a confidence score."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import similarity
from .titles import clean_title
from .urls import (
    UrlRecord,
    hosts_equal,
    loose_key,
    normalize_segments,
    parse_url,
    path_key,
    slug_key,
    tokens,
)

# Method names, in pipeline order.
OVERRIDE = "override"
EXACT = "exact-path"
EXACT_LOOSE = "exact-path-normalized"
NORM_PATH = "normalized-path"
SLUG = "slug"
TITLE = "title"
FUZZY = "fuzzy-path"
PARENT = "parent-path"
FALLBACK = "fallback"


@dataclass
class MatchConfig:
    min_confidence: float = 0.6
    title_threshold: float = 0.85
    fallback: Optional[str] = None
    new_base: Optional[str] = None
    max_candidates: int = 200


@dataclass
class Match:
    old: UrlRecord
    target: str
    method: str
    confidence: float
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "old": self.old.url,
            "new": self.target,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "note": self.note,
        }


@dataclass
class Unmatched:
    old: UrlRecord
    suggestions: List[Tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "old": self.old.url,
            "suggestions": [{"new": u, "score": round(s, 3)} for u, s in self.suggestions],
        }


@dataclass
class MapResult:
    matches: List[Match] = field(default_factory=list)
    unmatched: List[Unmatched] = field(default_factory=list)
    unchanged: List[UrlRecord] = field(default_factory=list)

    def by_method(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for m in self.matches:
            counts[m.method] = counts.get(m.method, 0) + 1
        return counts


class _Index:
    """Precomputed lookup tables over the new URL list."""

    def __init__(self, new: List[UrlRecord]) -> None:
        self.new = new
        self.exact: Dict[str, List[int]] = {}
        self.loose: Dict[str, List[int]] = {}
        self.pkey: Dict[str, List[int]] = {}
        self.slug: Dict[str, List[int]] = {}
        self.tok: Dict[str, List[int]] = {}
        self.keys: List[str] = []
        self.tokens: List[set] = []
        self.titles: List[str] = []
        for i, rec in enumerate(new):
            self.exact.setdefault(rec.request_uri, []).append(i)
            self.loose.setdefault(loose_key(rec), []).append(i)
            pk = path_key(rec.path)
            self.keys.append(pk)
            self.pkey.setdefault(pk, []).append(i)
            sk = slug_key(rec.path)
            if sk:
                self.slug.setdefault(sk, []).append(i)
            toks = tokens(rec.path)
            self.tokens.append(toks)
            for t in toks:
                self.tok.setdefault(t, []).append(i)
            self.titles.append(clean_title(rec.title))

    def candidates(self, toks: set, limit: int) -> List[int]:
        counts: Dict[int, int] = {}
        for t in toks:
            for i in self.tok.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        if not counts:
            return list(range(len(self.new))) if len(self.new) <= limit else []
        ranked = sorted(counts, key=lambda i: (-counts[i], i))
        return ranked[:limit]


def _same_page(old: UrlRecord, new: UrlRecord) -> bool:
    """Same request URI on the same host (an unknown host matches anything)."""
    return old.request_uri == new.request_uri and (
        old.host is None or new.host is None or old.host == new.host
    )


def _target_for(rec: UrlRecord) -> str:
    return rec.url


def _resolve_override(value: str, cfg: MatchConfig) -> str:
    rec = parse_url(value, base=cfg.new_base)
    return rec.url if rec else value


def _pick_best(old: UrlRecord, idxs: List[int], index: _Index) -> Tuple[int, int]:
    """Choose among several candidates by token overlap; returns (index, n_tied)."""
    old_tokens = tokens(old.path)
    old_segs = normalize_segments(old.path)
    scored = []
    for i in idxs:
        overlap = similarity.token_overlap(old_tokens, index.tokens[i])
        depth_penalty = abs(len(normalize_segments(index.new[i].path)) - len(old_segs)) * 0.01
        scored.append((overlap - depth_penalty, -i, i))
    scored.sort(reverse=True)
    best = scored[0][0]
    tied = sum(1 for s in scored if abs(s[0] - best) < 1e-9)
    return scored[0][2], tied


def match_one(old: UrlRecord, index: _Index, cfg: MatchConfig,
              overrides: Dict[str, str]) -> Tuple[Optional[Match], Optional[Unmatched], bool]:
    """Return ``(match, unmatched, unchanged)``; exactly one of them is set."""
    # 1. manual overrides (matched by full URL, path or loose key)
    for key in (old.url, old.request_uri, loose_key(old)):
        if key in overrides:
            return Match(old, _resolve_override(overrides[key], cfg), OVERRIDE, 1.0,
                         "manual override"), None, False
    for raw, value in overrides.items():
        rec = parse_url(raw)
        if rec and loose_key(rec) == loose_key(old) and hosts_equal(rec.host, old.host):
            return Match(old, _resolve_override(value, cfg), OVERRIDE, 1.0,
                         "manual override"), None, False

    # 2. exact path
    for i in index.exact.get(old.request_uri, ()):
        new = index.new[i]
        if _same_page(old, new):
            return None, None, True
        return Match(old, _target_for(new), EXACT, 1.0, "same path on new host"), None, False
    lk = loose_key(old)
    for i in index.loose.get(lk, ()):
        new = index.new[i]
        if _same_page(old, new):
            return None, None, True
        return Match(old, _target_for(new), EXACT_LOOSE, 0.98,
                     "differs by case or trailing slash"), None, False

    old_pk = path_key(old.path)
    old_slug = slug_key(old.path)
    old_tokens = tokens(old.path)

    # 3. normalised full path
    if old_pk:
        hits = index.pkey.get(old_pk, [])
        if hits:
            i, tied = _pick_best(old, hits, index)
            if tied == 1 or len(hits) == 1:
                return Match(old, _target_for(index.new[i]), NORM_PATH, 0.95,
                             "dates/IDs/extensions stripped"), None, False
            return Match(old, _target_for(index.new[i]), NORM_PATH, 0.8,
                         "ambiguous: %d candidates" % len(hits)), None, False

    # 4. slug (last segment)
    if old_slug:
        hits = index.slug.get(old_slug, [])
        if hits:
            i, tied = _pick_best(old, hits, index)
            if len(hits) == 1:
                return Match(old, _target_for(index.new[i]), SLUG, 0.9,
                             "unique slug match"), None, False
            conf = 0.8 if tied == 1 else 0.7
            return Match(old, _target_for(index.new[i]), SLUG, conf,
                         "ambiguous slug: %d candidates" % len(hits)), None, False

    # 5. title similarity
    old_title = clean_title(old.title)
    if old_title and any(index.titles):
        choices = [t for t in index.titles]
        found = similarity.best_matches(old_title, choices, limit=2, cutoff=cfg.title_threshold)
        found = [f for f in found if f[0]]
        if found:
            title, score, i = found[0]
            if len(found) == 1 or found[1][1] < score:
                return Match(old, _target_for(index.new[i]), TITLE, round(min(0.9, score * 0.9), 3),
                             "title similarity %.2f" % score), None, False

    # 6. fuzzy path + token overlap
    best_i, best_score = -1, 0.0
    scored: List[Tuple[float, int]] = []
    if old_pk:
        for i in index.candidates(old_tokens, cfg.max_candidates):
            sim = max(similarity.ratio(old_slug, slug_key(index.new[i].path)),
                      similarity.ratio(old_pk, index.keys[i]))
            ov = similarity.token_overlap(old_tokens, index.tokens[i])
            combined = 0.6 * sim + 0.4 * ov
            scored.append((combined, i))
        scored.sort(key=lambda t: (-t[0], t[1]))
        if scored:
            best_score, best_i = scored[0]
    conf = round(best_score * 0.85, 3)
    if best_i >= 0 and conf >= cfg.min_confidence:
        return Match(old, _target_for(index.new[best_i]), FUZZY, conf,
                     "path similarity %.2f" % best_score), None, False

    # 6b. parent path heuristic: /a/b/c -> /a/b -> /a
    segs = normalize_segments(old.path)
    for levels in range(1, len(segs)):
        parent = "/".join(segs[:-levels])
        hits = index.pkey.get(parent, [])
        if len(hits) == 1:
            conf = round(0.65 - 0.05 * (levels - 1), 3)
            if conf >= cfg.min_confidence:
                return Match(old, _target_for(index.new[hits[0]]), PARENT, conf,
                             "redirected to parent /%s" % parent), None, False
            break

    suggestions = [(index.new[i].url, round(sc * 0.85, 3)) for sc, i in scored[:3]]
    if cfg.fallback:
        target = _resolve_override(cfg.fallback, cfg)
        return Match(old, target, FALLBACK, 0.2, "no match; using fallback"), None, False
    return None, Unmatched(old, suggestions), False


def build_map(old: List[UrlRecord], new: List[UrlRecord],
              overrides: Optional[Dict[str, str]] = None,
              config: Optional[MatchConfig] = None) -> MapResult:
    """Run the full matching pipeline for every old URL."""
    cfg = config or MatchConfig()
    index = _Index(new)
    result = MapResult()
    for rec in old:
        match, unmatched, unchanged = match_one(rec, index, cfg, overrides or {})
        if match is not None:
            result.matches.append(match)
        elif unmatched is not None:
            result.unmatched.append(unmatched)
        elif unchanged:
            result.unchanged.append(rec)
    return result
