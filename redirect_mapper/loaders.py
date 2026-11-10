"""Load URL lists from sitemaps (index/gzip), CSV, text files and crawl exports."""
from __future__ import annotations

import csv
import gzip
import io
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

from .http import MAX_BYTES, fetch_bytes, make_session
from .urls import UrlRecord, loose_key, parse_url

MAX_SITEMAPS = 500
MAX_DEPTH = 3
URL_COLUMNS = ("address", "url", "loc", "old url", "old_url", "new url", "new_url",
               "source", "from", "page url", "landing page", "page")
TITLE_COLUMNS = ("title 1", "title", "page title")
STATUS_COLUMNS = ("status code", "status", "http status")


class LoadError(Exception):
    """Raised when an input cannot be read or parsed."""


def is_remote(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def read_source(source: str, session: Any = None) -> bytes:
    """Read a local path or HTTP(S) URL; gunzips transparently."""
    if is_remote(source):
        data = fetch_bytes(session or make_session(), source)
    else:
        try:
            with open(source, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
        except OSError as exc:
            raise LoadError("cannot read %s: %s" % (source, exc)) from exc
        if len(data) > MAX_BYTES:
            raise LoadError("%s is larger than %d bytes" % (source, MAX_BYTES))
    return maybe_gunzip(data)


def maybe_gunzip(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                out = gz.read(MAX_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise LoadError("invalid gzip data: %s" % exc) from exc
        if len(out) > MAX_BYTES:
            raise LoadError("decompressed data exceeds %d bytes" % MAX_BYTES)
        return out
    return data


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap_xml(data: bytes) -> Dict[str, List[str]]:
    """Parse one sitemap document.

    Returns ``{"urls": [...], "sitemaps": [...]}``: page URLs for a urlset and
    child sitemap locations for a sitemap index.
    """
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data[:65536].lower():
        raise LoadError("DOCTYPE/ENTITY declarations are not allowed in sitemaps")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LoadError("invalid sitemap XML: %s" % exc) from exc
    kind = _local_name(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise LoadError("not a sitemap (root element <%s>)" % kind)
    child_tag = "url" if kind == "urlset" else "sitemap"
    found: List[str] = []
    for child in root:
        if _local_name(child.tag) != child_tag:
            continue
        for node in child:
            if _local_name(node.tag) == "loc" and node.text and node.text.strip():
                found.append(node.text.strip())
                break
    return {"urls": found, "sitemaps": []} if kind == "urlset" else {"urls": [], "sitemaps": found}


def load_sitemap(source: str, session: Any = None, _depth: int = 0,
                 _seen: Optional[Set[str]] = None) -> List[str]:
    """Return all page URLs from a sitemap or sitemap index (recursive)."""
    seen = _seen if _seen is not None else set()
    if source in seen:
        return []
    seen.add(source)
    if len(seen) > MAX_SITEMAPS:
        raise LoadError("too many sitemaps (limit %d)" % MAX_SITEMAPS)
    parsed = parse_sitemap_xml(read_source(source, session))
    urls = list(parsed["urls"])
    if parsed["sitemaps"] and _depth >= MAX_DEPTH:
        raise LoadError("sitemap index nesting deeper than %d" % MAX_DEPTH)
    for child in parsed["sitemaps"]:
        target = child
        if not is_remote(source):
            # Offline convenience: a local index may reference children that sit
            # next to it under the same file name.
            local = os.path.join(os.path.dirname(source), os.path.basename(urlsplit(child).path))
            if os.path.isfile(local):
                target = local
        urls.extend(load_sitemap(target, session, _depth + 1, seen))
    return urls


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower().lstrip("﻿"))


def parse_csv_text(text: str, url_column: Optional[str] = None, only_ok: bool = False
                   ) -> List[Dict[str, Any]]:
    """Parse CSV text into ``[{"url", "title", "status"}]`` rows."""
    text = text.lstrip("﻿")
    if not text.strip():
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [_norm_header(c) for c in rows[0]]
    url_idx = title_idx = status_idx = None
    if url_column:
        want = _norm_header(url_column)
        if want not in header:
            raise LoadError("column %r not found; available: %s" % (url_column, ", ".join(header)))
        url_idx = header.index(want)
    else:
        for cand in URL_COLUMNS:
            if cand in header:
                url_idx = header.index(cand)
                break
    for cand in TITLE_COLUMNS:
        if cand in header:
            title_idx = header.index(cand)
            break
    for cand in STATUS_COLUMNS:
        if cand in header:
            status_idx = header.index(cand)
            break
    body = rows[1:]
    if url_idx is None:
        # Header-less file: treat the first column as URLs.
        url_idx, title_idx, status_idx, body = 0, None, None, rows
    out: List[Dict[str, Any]] = []
    for row in body:
        if url_idx >= len(row):
            continue
        url = row[url_idx].strip()
        if not url:
            continue
        status = None
        if status_idx is not None and status_idx < len(row):
            m = re.match(r"\d{3}", row[status_idx].strip())
            status = int(m.group(0)) if m else None
        if only_ok and status is not None and not 200 <= status < 300:
            continue
        title = None
        if title_idx is not None and title_idx < len(row):
            title = row[title_idx].strip() or None
        out.append({"url": url, "title": title, "status": status})
    return out


def parse_text_lines(text: str) -> List[Dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append({"url": line, "title": None, "status": None})
    return rows


def detect_format(source: str, data: bytes) -> str:
    """Return ``sitemap``, ``csv`` or ``txt`` for *source*."""
    lower = source.lower().split("?")[0]
    if lower.endswith((".csv", ".tsv")):
        return "csv"
    if lower.endswith(".txt"):
        return "txt"
    head = data[:2048].lstrip().lower()
    if head.startswith(b"<?xml") or b"<urlset" in head or b"<sitemapindex" in head:
        return "sitemap"
    if lower.endswith((".xml", ".xml.gz")):
        return "sitemap"
    return "txt"


def load_urls(source: str, fmt: str = "auto", session: Any = None, base: Optional[str] = None,
              url_column: Optional[str] = None, only_ok: bool = False) -> List[UrlRecord]:
    """Load one source into de-duplicated :class:`UrlRecord` objects."""
    if fmt not in ("auto", "sitemap", "csv", "txt"):
        raise LoadError("unknown format %r" % fmt)
    rows: List[Dict[str, Any]]
    if fmt == "sitemap" or (fmt == "auto" and _looks_like_sitemap_name(source)):
        rows = [{"url": u, "title": None, "status": None}
                for u in load_sitemap(source, session)]
    else:
        data = read_source(source, session)
        kind = detect_format(source, data) if fmt == "auto" else fmt
        if kind == "sitemap":
            rows = [{"url": u, "title": None, "status": None}
                    for u in load_sitemap(source, session)]
        else:
            text = data.decode("utf-8-sig", errors="replace")
            rows = (parse_csv_text(text, url_column, only_ok) if kind == "csv"
                    else parse_text_lines(text))
    records: List[UrlRecord] = []
    seen: Set[str] = set()
    for row in rows:
        rec = parse_url(row["url"], base=base)
        if rec is None:
            continue
        key = (rec.host or "") + loose_key(rec)
        if key in seen:
            continue
        seen.add(key)
        rec.title = row.get("title")
        rec.status = row.get("status")
        records.append(rec)
    return records


def _looks_like_sitemap_name(source: str) -> bool:
    lower = source.lower().split("?")[0]
    return lower.endswith((".xml", ".xml.gz")) and "csv" not in lower


def load_many(sources: List[str], **kwargs: Any) -> List[UrlRecord]:
    """Load several sources and merge them (first occurrence wins)."""
    merged: List[UrlRecord] = []
    seen: Set[str] = set()
    for src in sources:
        for rec in load_urls(src, **kwargs):
            key = (rec.host or "") + loose_key(rec)
            if key not in seen:
                seen.add(key)
                merged.append(rec)
    return merged


def load_overrides(path: str) -> Dict[str, str]:
    """Read a manual overrides CSV (``old,new`` columns; ``#`` comments allowed).

    Returns ``{old_as_written: new_as_written}``.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            lines = [
                ln for ln in fh.read().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")
            ]
    except OSError as exc:
        raise LoadError("cannot read overrides %s: %s" % (path, exc)) from exc
    if not lines:
        return {}
    rows = list(csv.reader(lines))
    header = [_norm_header(c) for c in rows[0]]
    old_names = ("old", "source", "from", "old url", "old_url")
    new_names = ("new", "target", "to", "new url", "new_url")
    old_idx = next((header.index(n) for n in old_names if n in header), None)
    new_idx = next((header.index(n) for n in new_names if n in header), None)
    body = rows[1:]
    if old_idx is None or new_idx is None:
        old_idx, new_idx, body = 0, 1, rows
    out: Dict[str, str] = {}
    for row in body:
        if len(row) <= max(old_idx, new_idx):
            continue
        old, new = row[old_idx].strip(), row[new_idx].strip()
        if old and new:
            out[old] = new
    return out
