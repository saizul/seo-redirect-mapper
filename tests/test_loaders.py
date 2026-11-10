import pytest

from conftest import FakeSession, index_xml, sitemap_xml
from redirect_mapper.loaders import (
    LoadError,
    detect_format,
    load_many,
    load_overrides,
    load_sitemap,
    load_urls,
    maybe_gunzip,
    parse_csv_text,
    parse_sitemap_xml,
    parse_text_lines,
)


def test_parse_sitemap_urlset():
    data = sitemap_xml(["https://a.com/1", "https://a.com/2"]).encode()
    out = parse_sitemap_xml(data)
    assert out == {"urls": ["https://a.com/1", "https://a.com/2"], "sitemaps": []}


def test_parse_sitemap_index():
    data = index_xml(["https://a.com/s1.xml"]).encode()
    assert parse_sitemap_xml(data)["sitemaps"] == ["https://a.com/s1.xml"]


def test_parse_sitemap_rejects_entities():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><urlset></urlset>'
    with pytest.raises(LoadError):
        parse_sitemap_xml(evil)


def test_parse_sitemap_rejects_non_sitemap_and_bad_xml():
    with pytest.raises(LoadError):
        parse_sitemap_xml(b"<html></html>")
    with pytest.raises(LoadError):
        parse_sitemap_xml(b"<urlset>")


def test_maybe_gunzip_roundtrip(gz):
    assert maybe_gunzip(gz("hello")) == b"hello"
    assert maybe_gunzip(b"plain") == b"plain"


def test_maybe_gunzip_invalid():
    with pytest.raises(LoadError):
        maybe_gunzip(b"\x1f\x8bnot really gzip")


def test_load_local_sitemap(write):
    path = write("s.xml", sitemap_xml(["https://a.com/x", "https://a.com/y"]))
    assert load_sitemap(path) == ["https://a.com/x", "https://a.com/y"]


def test_load_gzipped_sitemap(write, gz):
    path = write("s.xml.gz", gz(sitemap_xml(["https://a.com/x"])))
    assert load_sitemap(path) == ["https://a.com/x"]


def test_load_remote_sitemap_index_recurses():
    session = FakeSession(pages={
        "https://a.com/index.xml": index_xml(["https://a.com/p1.xml", "https://a.com/p2.xml"]),
        "https://a.com/p1.xml": sitemap_xml(["https://a.com/1"]),
        "https://a.com/p2.xml": sitemap_xml(["https://a.com/2", "https://a.com/3"]),
    })
    assert load_sitemap("https://a.com/index.xml", session) == [
        "https://a.com/1", "https://a.com/2", "https://a.com/3"]


def test_load_remote_gz_child_in_index(gz):
    session = FakeSession(pages={
        "https://a.com/index.xml": index_xml(["https://a.com/p1.xml.gz"]),
        "https://a.com/p1.xml.gz": gz(sitemap_xml(["https://a.com/1"])),
    })
    assert load_sitemap("https://a.com/index.xml", session) == ["https://a.com/1"]


def test_local_index_resolves_children_beside_it(write):
    write("child.xml", sitemap_xml(["https://a.com/c"]))
    idx = write("index.xml", index_xml(["https://a.com/child.xml"]))
    assert load_sitemap(idx) == ["https://a.com/c"]


def test_sitemap_cycle_is_not_infinite():
    session = FakeSession(pages={
        "https://a.com/a.xml": index_xml(["https://a.com/b.xml"]),
        "https://a.com/b.xml": index_xml(["https://a.com/a.xml"]),
    })
    assert load_sitemap("https://a.com/a.xml", session) == []


def test_remote_http_error_is_oserror():
    with pytest.raises(OSError):
        load_sitemap("https://a.com/missing.xml", FakeSession())


def test_missing_local_file_raises_loaderror(tmp_path):
    with pytest.raises(LoadError):
        load_sitemap(str(tmp_path / "nope.xml"))


def test_parse_csv_screaming_frog_style():
    text = "Address,Status Code,Title 1\nhttps://a.com/1,200,One\nhttps://a.com/2,404,Two\n"
    rows = parse_csv_text(text)
    assert [r["url"] for r in rows] == ["https://a.com/1", "https://a.com/2"]
    assert rows[0]["title"] == "One" and rows[1]["status"] == 404


def test_parse_csv_only_ok_filters_non_2xx():
    text = "Address,Status Code\nhttps://a.com/1,200\nhttps://a.com/2,301\nhttps://a.com/3,404\n"
    assert [r["url"] for r in parse_csv_text(text, only_ok=True)] == ["https://a.com/1"]


def test_parse_csv_semicolon_and_bom():
    text = "﻿url;title\nhttps://a.com/1;Hello\n"
    rows = parse_csv_text(text)
    assert rows[0]["url"] == "https://a.com/1" and rows[0]["title"] == "Hello"


def test_parse_csv_headerless_single_column():
    rows = parse_csv_text("/one\n/two\n")
    assert [r["url"] for r in rows] == ["/one", "/two"]


def test_parse_csv_explicit_column_and_missing_column():
    text = "id,link\n1,https://a.com/x\n"
    assert parse_csv_text(text, url_column="link")[0]["url"] == "https://a.com/x"
    with pytest.raises(LoadError):
        parse_csv_text(text, url_column="nope")


def test_parse_text_lines_ignores_comments_and_blanks():
    assert [r["url"] for r in parse_text_lines("# c\n\n/a\n  /b  \n")] == ["/a", "/b"]


def test_detect_format():
    assert detect_format("x.csv", b"") == "csv"
    assert detect_format("x.txt", b"") == "txt"
    assert detect_format("x", b'<?xml version="1.0"?><urlset>') == "sitemap"
    assert detect_format("x", b"/a\n/b") == "txt"


def test_load_urls_dedupes_and_parses(write):
    path = write("u.txt", "https://a.com/x\nhttps://a.com/x/\nhttps://a.com/y\nmailto:a@b.c\n")
    recs = load_urls(path)
    assert [r.path for r in recs] == ["/x", "/y"]


def test_load_urls_csv_carries_title_and_status(write):
    path = write("crawl.csv", "Address,Title 1,Status Code\nhttps://a.com/p,Page,200\n")
    rec = load_urls(path)[0]
    assert rec.title == "Page" and rec.status == 200


def test_load_urls_autodetects_sitemap_without_extension(write):
    path = write("sitemap", sitemap_xml(["https://a.com/z"]))
    assert load_urls(path)[0].path == "/z"


def test_load_urls_unknown_format():
    with pytest.raises(LoadError):
        load_urls("x", fmt="yaml")


def test_load_urls_base_applies_to_paths(write):
    path = write("p.txt", "/a\n/b\n")
    recs = load_urls(path, base="https://new.example.com")
    assert recs[0].url == "https://new.example.com/a"


def test_load_many_merges_without_duplicates(write):
    a = write("a.txt", "/1\n/2\n")
    b = write("b.txt", "/2\n/3\n")
    assert [r.path for r in load_many([a, b])] == ["/1", "/2", "/3"]


def test_load_overrides_with_header_and_comments(write):
    path = write("o.csv", "# note\nold,new\n/a,/b\n/c,https://x.com/d\n")
    assert load_overrides(path) == {"/a": "/b", "/c": "https://x.com/d"}


def test_load_overrides_headerless_and_aliases(write):
    assert load_overrides(write("o1.csv", "/a,/b\n")) == {"/a": "/b"}
    assert load_overrides(write("o2.csv", "source,target\n/a,/b\n")) == {"/a": "/b"}


def test_load_overrides_missing_file(tmp_path):
    with pytest.raises(LoadError):
        load_overrides(str(tmp_path / "missing.csv"))
