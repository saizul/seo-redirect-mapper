"""Redirect-map validation: duplicates, chains, loops and bad targets."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .http import RateLimiter
from .matching import Match
from .urls import UrlRecord, hosts_equal, loose_key, parse_url

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    severity: str
    code: str
    source: str
    message: str
    detail: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"severity": self.severity, "code": self.code,
                             "source": self.source, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class Entry:
    """A source -> target redirect, parsed for comparison."""

    source: UrlRecord
    target: UrlRecord
    raw_source: str
    raw_target: str


def entries_from_matches(matches: Iterable[Match]) -> List[Entry]:
    out = []
    for m in matches:
        tgt = parse_url(m.target)
        if tgt is not None:
            out.append(Entry(m.old, tgt, m.old.url, m.target))
    return out


def load_redirect_csv(path: str) -> List[Entry]:
    """Load ``source,target`` pairs (header optional, extra columns ignored)."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]
    out = []
    for i, row in enumerate(rows):
        if len(row) < 2:
            continue
        if i == 0 and row[0].strip().lower() in ("source", "source_url", "old", "from"):
            continue
        src, tgt = parse_url(row[0]), parse_url(row[1])
        if src is not None and tgt is not None:
            out.append(Entry(src, tgt, row[0].strip(), row[1].strip()))
    return out


def _key(rec: UrlRecord) -> str:
    return loose_key(rec)


def _build_lookup(entries: Sequence[Entry]) -> Dict[str, List[Entry]]:
    lookup: Dict[str, List[Entry]] = {}
    for e in entries:
        lookup.setdefault(_key(e.source), []).append(e)
    return lookup


def _next_hop(entry: Entry, lookup: Dict[str, List[Entry]]) -> Optional[Entry]:
    for cand in lookup.get(_key(entry.target), ()):
        if hosts_equal(cand.source.host, entry.target.host) and cand is not entry:
            return cand
    return None


def _same_url(a: UrlRecord, b: UrlRecord) -> bool:
    return _key(a) == _key(b) and (a.host is None or b.host is None or a.host == b.host)


def find_duplicates(entries: Sequence[Entry]) -> List[Issue]:
    issues: List[Issue] = []
    groups: Dict[Tuple[str, str], List[Entry]] = {}
    for e in entries:
        groups.setdefault((e.source.host or "", _key(e.source)), []).append(e)
    for (_host, _k), group in groups.items():
        if len(group) < 2:
            continue
        targets = {_key(g.target) + (g.target.host or "") for g in group}
        if len(targets) > 1:
            issues.append(Issue(ERROR, "duplicate-source", group[0].raw_source,
                                "source appears %d times with different targets" % len(group),
                                [g.raw_target for g in group]))
        else:
            issues.append(Issue(WARNING, "duplicate-source", group[0].raw_source,
                                "source appears %d times (same target)" % len(group)))
    return issues


def find_chains_and_loops(entries: Sequence[Entry]) -> List[Issue]:
    issues: List[Issue] = []
    lookup = _build_lookup(entries)
    reported_loops: Set[frozenset] = set()
    for e in entries:
        if _same_url(e.source, e.target):
            issues.append(Issue(ERROR, "self-redirect", e.raw_source,
                                "source and target are the same URL", [e.raw_target]))
            continue
        path = [e]
        visited = {id(e)}
        current = e
        while True:
            nxt = _next_hop(current, lookup)
            if nxt is None:
                break
            if id(nxt) in visited:
                members = frozenset(_key(x.source) for x in path)
                if members not in reported_loops:
                    reported_loops.add(members)
                    issues.append(Issue(ERROR, "loop", e.raw_source, "redirect loop detected",
                                        [x.raw_source for x in path] + [nxt.raw_source]))
                path = []
                break
            visited.add(id(nxt))
            path.append(nxt)
            current = nxt
        if len(path) > 1:
            final = path[-1].raw_target
            issues.append(Issue(WARNING, "chain", e.raw_source,
                                "redirect chain of %d hops; point directly to %s"
                                % (len(path), final),
                                [x.raw_source for x in path] + [final]))
    return issues


def flatten(entries: Sequence[Entry]) -> Dict[str, str]:
    """Map each raw source to its final destination (loops are left untouched)."""
    lookup = _build_lookup(entries)
    final: Dict[str, str] = {}
    for e in entries:
        current, seen = e, {id(e)}
        while True:
            nxt = _next_hop(current, lookup)
            if nxt is None or id(nxt) in seen:
                break
            seen.add(id(nxt))
            current = nxt
        if current is not e and not _is_loop(e, current, lookup):
            final[e.raw_source] = current.raw_target
    return final


def _is_loop(first: Entry, last: Entry, lookup: Dict[str, List[Entry]]) -> bool:
    nxt = _next_hop(last, lookup)
    return nxt is not None


def find_unknown_targets(entries: Sequence[Entry], known: Sequence[UrlRecord]) -> List[Issue]:
    known_keys = {(_key(r)) for r in known}
    issues = []
    for e in entries:
        if _key(e.target) not in known_keys and _key(e.target) != "/":
            issues.append(Issue(WARNING, "target-not-in-new-list", e.raw_source,
                                "target is not in the new URL list", [e.raw_target]))
    return issues


def find_live_sources(entries: Sequence[Entry], known: Sequence[UrlRecord]) -> List[Issue]:
    """Sources that still exist as live pages on the new site."""
    known_keys = {(r.host or "", _key(r)) for r in known}
    issues = []
    for e in entries:
        if (e.source.host or "", _key(e.source)) in known_keys:
            issues.append(Issue(WARNING, "source-is-live", e.raw_source,
                                "source URL also exists in the new URL list", [e.raw_target]))
    return issues


def check_targets(entries: Sequence[Entry], session: Any, rate: float = 5.0,
                  timeout: float = 10.0, limiter: Optional[RateLimiter] = None,
                  progress: Optional[Callable[[int, int], None]] = None) -> List[Issue]:
    """HEAD every distinct absolute target (rate limited); report 3xx/4xx/5xx."""
    limiter = limiter or RateLimiter(rate)
    targets: Dict[str, List[Entry]] = {}
    for e in entries:
        if e.target.host:
            targets.setdefault(e.target.url, []).append(e)
    issues: List[Issue] = []
    total = len(targets)
    for n, (url, users) in enumerate(targets.items(), 1):
        limiter.wait()
        try:
            resp = session.head(url, allow_redirects=False, timeout=timeout)
            status = resp.status_code
        except Exception as exc:  # report, never crash the run
            issues.append(Issue(ERROR, "target-unreachable", users[0].raw_source,
                                "HEAD %s failed: %s" % (url, exc.__class__.__name__), [url]))
            continue
        if status in (405, 501):  # HEAD unsupported: retry with GET
            try:
                limiter.wait()
                resp = session.get(url, allow_redirects=False, timeout=timeout)
                status = resp.status_code
            except Exception:
                status = 0
        if 300 <= status < 400:
            loc = ""
            try:
                loc = resp.headers.get("Location", "")
            except Exception:
                pass
            issues.append(Issue(ERROR, "target-redirects", users[0].raw_source,
                                "target returns %d (redirect to %s)" % (status, loc or "?"),
                                [url] + [u.raw_source for u in users[1:5]]))
        elif status >= 400:
            issues.append(Issue(ERROR, "target-status", users[0].raw_source,
                                "target returns HTTP %d" % status,
                                [url] + [u.raw_source for u in users[1:5]]))
        if progress:
            progress(n, total)
    return issues


def validate(entries: Sequence[Entry], new_urls: Optional[Sequence[UrlRecord]] = None,
             session: Any = None, check: bool = False, rate: float = 5.0,
             limiter: Optional[RateLimiter] = None) -> List[Issue]:
    """Run all validators and return issues sorted errors first."""
    issues = find_duplicates(entries) + find_chains_and_loops(entries)
    if new_urls:
        issues += find_unknown_targets(entries, new_urls)
        issues += find_live_sources(entries, new_urls)
    if check and session is not None:
        issues += check_targets(entries, session, rate=rate, limiter=limiter)
    order = {ERROR: 0, WARNING: 1}
    issues.sort(key=lambda i: (order.get(i.severity, 2), i.code, i.source))
    return issues


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity == ERROR for i in issues)
