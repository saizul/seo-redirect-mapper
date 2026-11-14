"""Post-deployment checks: request every old URL and confirm the live redirect.

``validate`` looks at a redirect map on paper; ``verify`` looks at what the
server actually answers once the rules are deployed. Each distinct source is
requested without following redirects, the ``Location`` chain is walked hop by
hop (bounded by ``max_hops``) and the final destination is compared with the
expected target.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

from .http import RateLimiter
from .urls import UrlRecord, parse_url
from .validate import ERROR, WARNING, Entry, Issue, load_redirect_csv

PERMANENT = (301, 308)
TEMPORARY = (302, 303, 307)

OK = "ok"
STATUS_ORDER = {ERROR: 0, WARNING: 1, OK: 2}


class VerifyInputError(ValueError):
    """The redirect list could not be read."""


@dataclass
class Hop:
    url: str
    status: int
    location: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"url": self.url, "status": self.status}
        if self.location:
            d["location"] = self.location
        return d


@dataclass
class VerifyResult:
    """Outcome for one source URL."""

    source: str
    request_url: str
    expected: str
    status: str = OK
    code: str = "ok"
    message: str = ""
    final_url: str = ""
    hops: List[Hop] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "request_url": self.request_url,
            "expected": self.expected,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "final_url": self.final_url,
            "hops": [h.to_dict() for h in self.hops],
        }

    def to_issue(self) -> Optional[Issue]:
        if self.status == OK:
            return None
        detail = [self.request_url] + ["%d %s" % (h.status, h.location or h.url)
                                       for h in self.hops]
        return Issue(self.status, self.code, self.source, self.message, detail)


def load_redirects(path: str) -> List[Entry]:
    """Load redirects from a ``source,target`` CSV or a ``report.json`` from ``map``."""
    if path.lower().endswith(".json"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except ValueError as exc:
            raise VerifyInputError("%s is not valid JSON: %s" % (path, exc)) from exc
        matches = data.get("matches") if isinstance(data, dict) else None
        if not isinstance(matches, list):
            raise VerifyInputError("%s has no \"matches\" list (expected a map report)" % path)
        out: List[Entry] = []
        for item in matches:
            if not isinstance(item, dict):
                continue
            raw_src, raw_tgt = str(item.get("old") or ""), str(item.get("new") or "")
            src, tgt = parse_url(raw_src), parse_url(raw_tgt)
            if src is not None and tgt is not None:
                out.append(Entry(src, tgt, raw_src, raw_tgt))
        return out
    return load_redirect_csv(path)


def _origin(base: str) -> Tuple[str, str]:
    parts = urlsplit(base if "://" in base else "https://" + base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise VerifyInputError("invalid base URL: %r" % base)
    return parts.scheme, parts.netloc.lower()


def request_url_for(source: UrlRecord, base: Optional[str]) -> Optional[str]:
    """URL to request for *source*; ``base`` replaces (or supplies) scheme and host."""
    if base:
        scheme, host = _origin(base)
        return "%s://%s%s" % (scheme, host, source.request_uri)
    if source.host:
        return source.url
    return None


def _same(a: Optional[UrlRecord], b: Optional[UrlRecord]) -> bool:
    if a is None or b is None:
        return False
    scheme_a = a.url.split("://", 1)[0] if a.host else ""
    scheme_b = b.url.split("://", 1)[0] if b.host else ""
    return (scheme_a, a.host, a.path, a.query) == (scheme_b, b.host, b.path, b.query)


def _loosely_same(a: Optional[UrlRecord], b: Optional[UrlRecord]) -> bool:
    """Same host and path ignoring case, trailing slash and http/https."""
    if a is None or b is None or a.host != b.host:
        return False
    pa, pb = a.decoded_path.lower(), b.decoded_path.lower()
    if len(pa) > 1:
        pa = pa.rstrip("/") or "/"
    if len(pb) > 1:
        pb = pb.rstrip("/") or "/"
    return (pa, a.query) == (pb, b.query)


def _request(session: Any, method: str, url: str, timeout: float,
             limiter: RateLimiter) -> Tuple[int, str]:
    limiter.wait()
    if method == "HEAD":
        resp = session.head(url, allow_redirects=False, timeout=timeout)
        if resp.status_code in (405, 501):  # HEAD unsupported: retry with GET
            limiter.wait()
            resp = session.get(url, allow_redirects=False, timeout=timeout, stream=True)
    else:
        resp = session.get(url, allow_redirects=False, timeout=timeout, stream=True)
    location = ""
    try:
        location = resp.headers.get("Location", "") or ""
    except Exception:  # odd header objects: treat as no Location
        location = ""
    close = getattr(resp, "close", None)
    if callable(close):
        close()
    return int(resp.status_code), location


def verify_entry(entry: Entry, session: Any, base: Optional[str] = None,
                 method: str = "HEAD", max_hops: int = 5, timeout: float = 10.0,
                 limiter: Optional[RateLimiter] = None) -> VerifyResult:
    """Request one source and classify what the live server does with it."""
    limiter = limiter or RateLimiter(0)
    req = request_url_for(entry.source, base)
    expected_raw = entry.raw_target
    result = VerifyResult(entry.raw_source, req or entry.raw_source, expected_raw)
    if req is None:
        result.status, result.code = ERROR, "no-host"
        result.message = "source has no host; pass --base to say where to request it"
        return result
    expected = parse_url(urljoin(req, expected_raw))
    result.expected = expected.url if expected else expected_raw

    url = req
    seen = {url}
    while True:
        try:
            status, location = _request(session, method, url, timeout, limiter)
        except Exception as exc:  # report, never crash the run
            result.status, result.code = ERROR, "unreachable"
            result.message = "%s %s failed: %s" % (method, url, exc.__class__.__name__)
            result.final_url = url
            return result
        nxt = urljoin(url, location) if location else ""
        result.hops.append(Hop(url, status, nxt))
        if not (300 <= status < 400) or not nxt:
            break
        if urlsplit(nxt).scheme not in ("http", "https"):
            result.status, result.code = ERROR, "bad-location"
            result.message = "redirects to a non-HTTP location: %s" % nxt
            result.final_url = nxt
            return result
        if nxt in seen:
            result.status, result.code = ERROR, "loop"
            result.message = "live redirect loop back to %s" % nxt
            result.final_url = nxt
            return result
        if len(result.hops) >= max_hops:
            result.status, result.code = ERROR, "too-many-hops"
            result.message = "still redirecting after %d hop(s)" % len(result.hops)
            result.final_url = nxt
            return result
        seen.add(nxt)
        url = nxt

    first, last = result.hops[0], result.hops[-1]
    result.final_url = last.url
    final = parse_url(last.url)
    redirects = [h for h in result.hops if 300 <= h.status < 400]

    if not redirects:
        result.status, result.code = ERROR, "not-redirecting"
        result.message = "source answers HTTP %d instead of redirecting" % first.status
        return result
    if 300 <= last.status < 400:  # 3xx without a Location header
        result.status, result.code = ERROR, "missing-location"
        result.message = "HTTP %d without a Location header" % last.status
        return result
    if not _same(final, expected):
        if _loosely_same(final, expected):
            result.status, result.code = WARNING, "target-variant"
            result.message = "ends at %s, a case/slash/scheme variant of %s" % (
                last.url, result.expected)
        else:
            result.status, result.code = ERROR, "wrong-target"
            result.message = "ends at %s, expected %s" % (last.url, result.expected)
        return result
    if last.status >= 400:
        result.status, result.code = ERROR, "target-status"
        result.message = "destination returns HTTP %d" % last.status
        return result
    if first.status not in PERMANENT:
        result.status, result.code = WARNING, "temporary-redirect"
        result.message = "first hop is HTTP %d; use 301 or 308 for SEO" % first.status
        return result
    if len(redirects) > 1:
        result.status, result.code = WARNING, "chain"
        result.message = "reaches the target in %d hops; redirect directly" % len(redirects)
        return result
    result.message = "HTTP %d to %s" % (first.status, result.expected)
    return result


def verify(entries: Sequence[Entry], session: Any, base: Optional[str] = None,
           method: str = "HEAD", rate: float = 5.0, max_hops: int = 5,
           timeout: float = 10.0, limit: Optional[int] = None,
           limiter: Optional[RateLimiter] = None,
           progress: Optional[Callable[[int, int], None]] = None) -> List[VerifyResult]:
    """Verify each distinct source (first target wins), rate limited."""
    if base:
        _origin(base)  # fail fast on a bad base URL
    method = method.upper()
    if method not in ("HEAD", "GET"):
        raise VerifyInputError("method must be HEAD or GET")
    limiter = limiter or RateLimiter(rate)
    todo: List[Entry] = []
    seen = set()
    for e in entries:
        key = request_url_for(e.source, base) or e.raw_source
        if key in seen:
            continue
        seen.add(key)
        todo.append(e)
    if limit is not None and limit >= 0:
        todo = todo[:limit]
    results = []
    for n, e in enumerate(todo, 1):
        results.append(verify_entry(e, session, base=base, method=method,
                                    max_hops=max(1, max_hops), timeout=timeout,
                                    limiter=limiter))
        if progress:
            progress(n, len(todo))
    results.sort(key=lambda r: (STATUS_ORDER.get(r.status, 3), r.code, r.source))
    return results


def summarize(results: Sequence[VerifyResult]) -> Dict[str, Any]:
    by_code: Dict[str, int] = {}
    for r in results:
        by_code[r.code] = by_code.get(r.code, 0) + 1
    return {
        "checked": len(results),
        "ok": sum(1 for r in results if r.status == OK),
        "warnings": sum(1 for r in results if r.status == WARNING),
        "errors": sum(1 for r in results if r.status == ERROR),
        "by_code": dict(sorted(by_code.items())),
    }


def to_report(results: Sequence[VerifyResult], meta: Optional[Dict[str, Any]] = None) -> str:
    data = {"summary": summarize(results), "meta": meta or {},
            "results": [r.to_dict() for r in results]}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
