"""Optional page-title fetching for title-similarity matching."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup

from .http import RateLimiter
from .urls import UrlRecord

_SEPARATORS = re.compile(r"\s+[|–—»:-]\s+")


def clean_title(title: Optional[str]) -> str:
    """Lower-case a title and drop a trailing/leading site-name part."""
    if not title:
        return ""
    title = re.sub(r"\s+", " ", title).strip()
    parts = [p for p in _SEPARATORS.split(title) if p.strip()]
    if len(parts) > 1:
        title = max(parts, key=len)
    return title.strip().lower()


def extract_title(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("title")
    text = node.get_text(" ", strip=True) if node else ""
    if not text:
        og = soup.find("meta", attrs={"property": "og:title"})
        text = (og.get("content") or "").strip() if og else ""
    return text or None


def fetch_titles(records: Iterable[UrlRecord], session: Any, rate: float = 2.0,
                 timeout: float = 15.0, limiter: Optional[RateLimiter] = None) -> int:
    """Fill ``record.title`` for absolute URLs lacking one. Returns titles fetched."""
    limiter = limiter or RateLimiter(rate)
    fetched = 0
    for rec in records:
        if rec.title or not rec.host:
            continue
        limiter.wait()
        try:
            resp = session.get(rec.url, timeout=timeout)
        except Exception:  # network errors must not abort the whole run
            continue
        if getattr(resp, "status_code", 200) >= 400:
            continue
        ctype = str(getattr(resp, "headers", {}).get("Content-Type", "text/html")).lower()
        if "html" not in ctype:
            continue
        title = extract_title(resp.text[:500_000])
        if title:
            rec.title = title
            fetched += 1
    return fetched
