"""HTTP helpers: a shared session, rate limiting and bounded downloads."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import requests

USER_AGENT = "redirect-mapper/0.1 (+https://saizul.com)"
MAX_BYTES = 50 * 1024 * 1024


class RateLimiter:
    """Allow at most ``rate`` calls per second (``rate <= 0`` disables limiting)."""

    def __init__(
        self,
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 1.0 / rate if rate and rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next: Optional[float] = None

    def wait(self) -> None:
        if not self.interval:
            return
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self.interval


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_bytes(session: Any, url: str, timeout: float = 30.0) -> bytes:
    """GET *url* and return the body, refusing responses larger than MAX_BYTES."""
    resp = session.get(url, timeout=timeout)
    status = getattr(resp, "status_code", 200)
    if status >= 400:
        raise OSError("GET %s returned HTTP %s" % (url, status))
    content = resp.content
    if len(content) > MAX_BYTES:
        raise OSError("response for %s is larger than %d bytes" % (url, MAX_BYTES))
    return content
