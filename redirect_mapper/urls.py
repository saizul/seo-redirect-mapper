"""URL parsing and slug normalisation helpers."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Set
from urllib.parse import quote, unquote, urlsplit

from .translit import transliterate

PATH_SAFE = "/-._~!*()':@&=+,"
QUERY_SAFE = "=&,+:@/-._~!*()'"
STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "for", "on", "is", "html", "php"}

_EXT_RE = re.compile(r"\.(html?|php\d?|aspx?|jsp|shtml|cfm)$")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_SMALL_NUM_RE = re.compile(r"\d{1,2}")
_FULL_DATE_RE = re.compile(r"(?:19|20)\d{2}[-_]\d{1,2}(?:[-_]\d{1,2})?")
_LEAD_ID_RE = re.compile(r"^\d{3,}[-_]+")
_TRAIL_ID_RE = re.compile(r"[-_]+\d{3,}$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_OPAQUE_RE = re.compile(r"^(mailto|tel|sms|javascript|data):", re.I)


@dataclass
class UrlRecord:
    """One URL from an input list."""

    url: str
    host: Optional[str]
    path: str
    query: str = ""
    title: Optional[str] = None
    status: Optional[int] = None

    @property
    def request_uri(self) -> str:
        return self.path + ("?" + self.query if self.query else "")

    @property
    def decoded_path(self) -> str:
        return unquote(self.path)


def canonical_path(path: str) -> str:
    """Percent-encode consistently, collapse ``//`` and force a leading slash."""
    path = re.sub(r"/{2,}", "/", path or "/")
    if not path.startswith("/"):
        path = "/" + path
    return quote(unquote(path), safe=PATH_SAFE)


def canonical_query(query: str) -> str:
    return quote(unquote(query), safe=QUERY_SAFE) if query else ""


def strip_www(host: Optional[str]) -> Optional[str]:
    if host and host.startswith("www."):
        return host[4:]
    return host


def parse_url(raw: str, base: Optional[str] = None) -> Optional[UrlRecord]:
    """Parse a URL or a bare path into a :class:`UrlRecord`.

    Returns ``None`` for empty or non-HTTP input. ``base`` (e.g.
    ``https://new.example.com``) supplies the host for bare paths.
    """
    raw = (raw or "").strip()
    if not raw or raw.startswith("#") or _OPAQUE_RE.match(raw):
        return None
    if _SCHEME_RE.match(raw):
        parts = urlsplit(raw)
        if parts.scheme.lower() not in ("http", "https"):
            return None
        host = (parts.hostname or "").lower() or None
        if host and parts.port:
            host = "%s:%d" % (host, parts.port)
        scheme = parts.scheme.lower()
    elif raw.startswith("//"):
        parts = urlsplit("https:" + raw)
        host = (parts.hostname or "").lower() or None
        scheme = "https"
    else:
        if not raw.startswith("/"):
            raw = "/" + raw
        parts = urlsplit(raw)
        host = None
        scheme = "https"
        if base:
            bp = urlsplit(base)
            host = (bp.netloc or "").lower() or None
            scheme = bp.scheme or "https"
    path = canonical_path(parts.path)
    query = canonical_query(parts.query)
    uri = path + ("?" + query if query else "")
    url = "%s://%s%s" % (scheme, host, uri) if host else uri
    return UrlRecord(url=url, host=host, path=path, query=query)


def loose_key(rec: UrlRecord) -> str:
    """Case-insensitive, trailing-slash-insensitive path (+query) key."""
    path = rec.decoded_path.lower()
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path + ("?" + rec.query.lower() if rec.query else "")


def hosts_equal(a: Optional[str], b: Optional[str]) -> bool:
    """True if hosts match ignoring ``www.``; unknown hosts match anything."""
    if not a or not b:
        return True
    return strip_www(a) == strip_www(b)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_segments(path: str) -> List[str]:
    """Reduce a URL path to comparable slug segments.

    Lower-cases, transliterates Bangla, drops date prefixes (``/2019/05/12/``),
    file extensions, ``index`` files, numeric IDs and ``123-`` / ``-123`` ID
    affixes, and collapses punctuation to single hyphens.
    """
    text = unquote(path.split("?")[0])
    text = _fold(transliterate(text)).lower()
    raw = [s for s in text.split("/") if s]
    out: List[str] = []
    i, n = 0, len(raw)
    while i < n:
        seg = raw[i]
        if _YEAR_RE.fullmatch(seg) and i + 1 < n and _SMALL_NUM_RE.fullmatch(raw[i + 1]):
            i += 2
            if i < n - 1 and _SMALL_NUM_RE.fullmatch(raw[i]):
                i += 1
            continue
        if i == n - 1:
            seg = _EXT_RE.sub("", seg)
        i += 1
        if _FULL_DATE_RE.fullmatch(seg) or seg.isdigit():
            continue
        stripped = _LEAD_ID_RE.sub("", seg)
        seg = stripped or seg
        stripped = _TRAIL_ID_RE.sub("", seg)
        seg = stripped or seg
        seg = re.sub(r"[^a-z0-9]+", "-", seg).strip("-")
        if not seg:
            continue
        out.append(seg)
    if out and out[-1] == "index":
        out.pop()
    return out


def path_key(path: str) -> str:
    """Normalised full-path key, e.g. ``/blog/2019/05/My_Post.html`` -> ``blog/my-post``."""
    return "/".join(normalize_segments(path))


def slug_key(path: str) -> str:
    """Normalised last segment only."""
    segs = normalize_segments(path)
    return segs[-1] if segs else ""


def tokens(path: str) -> Set[str]:
    """Meaningful word tokens of a path (for overlap scoring)."""
    out: Set[str] = set()
    for seg in normalize_segments(path):
        for tok in seg.split("-"):
            if len(tok) > 1 and tok not in STOPWORDS:
                out.add(tok)
    return out
