import pytest

from conftest import FakeResponse, FakeSession
from redirect_mapper import similarity
from redirect_mapper.http import RateLimiter, fetch_bytes
from redirect_mapper.titles import clean_title, extract_title, fetch_titles
from redirect_mapper.urls import parse_url


def test_ratio_identical_and_empty(backend):
    assert similarity.ratio("abc", "abc") == 1.0
    assert similarity.ratio("", "abc") == 0.0


def test_ratio_orders_by_closeness(backend):
    close = similarity.ratio("technical-seo", "technical-seo-tips")
    far = similarity.ratio("technical-seo", "contact")
    assert close > far


def test_best_matches_returns_sorted_with_indexes(backend):
    choices = ["contact", "technical-seo-checklist", "seo-checklist"]
    found = similarity.best_matches("seo-checklist", choices, limit=2)
    assert found[0][0] == "seo-checklist" and found[0][2] == 2
    assert len(found) == 2
    assert found[0][1] >= found[1][1]


def test_best_matches_cutoff_filters(backend):
    assert similarity.best_matches("zzzz", ["contact", "about"], cutoff=0.9) == []


def test_best_matches_empty_inputs(backend):
    assert similarity.best_matches("", ["a"]) == []
    assert similarity.best_matches("a", []) == []


def test_token_overlap():
    assert similarity.token_overlap(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert similarity.token_overlap([], ["a"]) == 0.0


def test_set_backend_validation():
    with pytest.raises(ValueError):
        similarity.set_backend("nope")


def test_backend_switch_reports_name():
    previous = similarity.backend()
    similarity.set_backend("difflib")
    assert similarity.backend() == "difflib"
    similarity.set_backend(previous)


def test_rate_limiter_sleeps_between_calls():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2, clock=lambda: now[0], sleep=sleep)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert slept == [0.5, 0.5]


def test_rate_limiter_disabled():
    slept = []
    limiter = RateLimiter(0, sleep=slept.append)
    for _ in range(5):
        limiter.wait()
    assert slept == []


def test_fetch_bytes_http_error():
    with pytest.raises(OSError):
        fetch_bytes(FakeSession(), "https://a.com/x")


def test_fetch_bytes_ok():
    assert fetch_bytes(FakeSession({"https://a.com/x": b"hi"}), "https://a.com/x") == b"hi"


def test_clean_title_strips_site_suffix():
    assert clean_title("Technical SEO Checklist | Example Site") == "technical seo checklist"
    assert clean_title("  Spaced   Out  ") == "spaced out"
    assert clean_title(None) == ""


def test_extract_title_prefers_title_tag_then_og():
    assert extract_title("<html><head><title> Hello </title></head></html>") == "Hello"
    assert extract_title('<meta property="og:title" content="OG Title">') == "OG Title"
    assert extract_title("<p>no title</p>") is None


def test_fetch_titles_fills_missing_and_rate_limits():
    now = [0.0]
    slept = []
    session = FakeSession({
        "https://a.com/1": "<title>One</title>",
        "https://a.com/2": "<title>Two</title>",
    })
    recs = [parse_url("https://a.com/1"), parse_url("https://a.com/2"), parse_url("/path-only")]
    limiter = RateLimiter(1, clock=lambda: now[0],
                          sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    assert fetch_titles(recs, session, limiter=limiter) == 2
    assert recs[0].title == "One" and recs[2].title is None
    assert len(slept) == 1


def test_fetch_titles_skips_existing_errors_and_non_html():
    session = FakeSession({
        "https://a.com/pdf": FakeResponse(b"x", headers={"Content-Type": "application/pdf"}),
    })
    have = parse_url("https://a.com/have")
    have.title = "Existing"
    recs = [have, parse_url("https://a.com/pdf"), parse_url("https://a.com/missing")]
    assert fetch_titles(recs, session, rate=0) == 0
    assert have.title == "Existing"
    assert ("GET", "https://a.com/have") not in session.calls
