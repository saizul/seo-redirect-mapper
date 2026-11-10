import pytest

from redirect_mapper.translit import has_bangla, transliterate
from redirect_mapper.urls import (
    canonical_path,
    hosts_equal,
    loose_key,
    normalize_segments,
    parse_url,
    path_key,
    slug_key,
    tokens,
)


def test_has_bangla():
    assert has_bangla("ঢাকা")
    assert not has_bangla("dhaka")


def test_transliterate_passthrough_for_latin():
    assert transliterate("hello-world") == "hello-world"


def test_transliterate_basic_word():
    assert transliterate("বাংলা") == "bangla"


def test_transliterate_digits():
    assert transliterate("২০২৪") == "2024"


def test_transliterate_drops_hasanta_and_keeps_ascii():
    out = transliterate("সিইও-guide")
    assert out.endswith("-guide")
    assert out.isascii()


def test_transliterate_nukta_forms():
    assert transliterate("ড়") == "r"
    assert transliterate("য়") == "y"


@pytest.mark.parametrize(
    "raw,expected_host,expected_path",
    [
        ("https://Example.com/About", "example.com", "/About"),
        ("http://example.com", "example.com", "/"),
        ("//cdn.example.com/a", "cdn.example.com", "/a"),
        ("/just/a/path", None, "/just/a/path"),
        ("relative/path", None, "/relative/path"),
        ("https://example.com//double//slash", "example.com", "/double/slash"),
    ],
)
def test_parse_url_variants(raw, expected_host, expected_path):
    rec = parse_url(raw)
    assert rec.host == expected_host
    assert rec.path == expected_path


@pytest.mark.parametrize("raw", ["", "   ", "#frag", "mailto:a@example.com", "ftp://example.com/x"])
def test_parse_url_rejects_non_http(raw):
    assert parse_url(raw) is None


def test_parse_url_keeps_query_and_drops_fragment():
    rec = parse_url("https://example.com/index.php?p=12#top")
    assert rec.query == "p=12"
    assert rec.request_uri == "/index.php?p=12"
    assert "#" not in rec.url


def test_parse_url_applies_base_to_paths():
    rec = parse_url("/x/", base="https://new.example.com")
    assert rec.url == "https://new.example.com/x/"
    assert rec.host == "new.example.com"


def test_parse_url_port_is_kept():
    assert parse_url("http://example.com:8080/a").host == "example.com:8080"


def test_canonical_path_encodes_unicode_consistently():
    a = canonical_path("/ঢাকা")
    b = canonical_path("/%E0%A6%A2%E0%A6%BE%E0%A6%95%E0%A6%BE")
    assert a == b
    assert a.startswith("/%E0")


def test_canonical_path_encodes_dangerous_characters():
    p = canonical_path('/a b";${x}')
    assert " " not in p and '"' not in p and "$" not in p and ";" not in p


def test_loose_key_ignores_case_and_trailing_slash():
    a = parse_url("https://x.com/About-Us/")
    b = parse_url("https://x.com/about-us")
    assert loose_key(a) == loose_key(b)
    assert loose_key(parse_url("https://x.com/")) == "/"


def test_hosts_equal_ignores_www_and_unknown():
    assert hosts_equal("www.a.com", "a.com")
    assert hosts_equal(None, "a.com")
    assert not hosts_equal("a.com", "b.com")


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/2019/05/12/my-post/", ["my-post"]),
        ("/2019/05/my-post", ["my-post"]),
        ("/blog/My_Post.HTML", ["blog", "my-post"]),
        ("/post/12345", ["post"]),
        ("/123-my-post", ["my-post"]),
        ("/my-post-98765", ["my-post"]),
        ("/10-best-tools", ["10-best-tools"]),
        ("/a/index.php", ["a"]),
        ("/2019-05-12/post", ["post"]),
        ("/", []),
        ("/Hello%20World", ["hello-world"]),
    ],
)
def test_normalize_segments(path, expected):
    assert normalize_segments(path) == expected


def test_normalize_segments_transliterates_bangla():
    assert normalize_segments("/%E0%A6%AC%E0%A6%BE%E0%A6%82%E0%A6%B2%E0%A6%BE") == ["bangla"]


def test_normalize_segments_folds_accents():
    assert normalize_segments("/café-guide") == ["cafe-guide"]


def test_path_key_and_slug_key():
    assert path_key("/blog/2020/01/Post_One.html") == "blog/post-one"
    assert slug_key("/blog/2020/01/Post_One.html") == "post-one"
    assert slug_key("/") == ""


def test_tokens_drop_stopwords_and_single_chars():
    assert tokens("/the-art-of-seo-x") == {"art", "seo"}
