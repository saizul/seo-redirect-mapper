import gzip

import pytest

from redirect_mapper import similarity


class FakeResponse:
    def __init__(self, content=b"", status=200, headers=None, text=None):
        self.content = content
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html"}
        self.text = text if text is not None else content.decode("utf-8", "replace")


class FakeSession:
    """Minimal requests.Session stand-in keyed by URL."""

    def __init__(self, pages=None, heads=None):
        self.pages = pages or {}
        self.heads = heads or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        if url not in self.pages:
            return FakeResponse(b"", 404)
        value = self.pages[url]
        if isinstance(value, FakeResponse):
            return value
        data = value if isinstance(value, bytes) else value.encode()
        return FakeResponse(data)

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url))
        value = self.heads.get(url, 200)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return FakeResponse(b"", value[0], headers={"Location": value[1]})
        return FakeResponse(b"", value)


def sitemap_xml(urls):
    body = "".join("<url><loc>%s</loc></url>" % u for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % body)


def index_xml(urls):
    body = "".join("<sitemap><loc>%s</loc></sitemap>" % u for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</sitemapindex>'
            % body)


@pytest.fixture
def fake_session():
    return FakeSession()


@pytest.fixture
def write(tmp_path):
    def _write(name, content, binary=False):
        path = tmp_path / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return str(path)
    return _write


@pytest.fixture
def gz():
    return lambda text: gzip.compress(text.encode("utf-8"))


@pytest.fixture(params=["difflib", "rapidfuzz"])
def backend(request):
    if request.param == "rapidfuzz" and not similarity.HAS_RAPIDFUZZ:
        pytest.skip("rapidfuzz not installed")
    previous = similarity.backend()
    similarity.set_backend(request.param)
    yield request.param
    similarity.set_backend(previous)
