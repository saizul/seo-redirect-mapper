import json
import os

import pytest
import requests

from conftest import FakeResponse, FakeSession
from redirect_mapper.cli import main
from redirect_mapper.http import RateLimiter
from redirect_mapper.urls import parse_url
from redirect_mapper.validate import Entry
from redirect_mapper.verify import (
    VerifyInputError,
    load_redirects,
    request_url_for,
    summarize,
    to_report,
    verify,
    verify_entry,
)

OLD = "https://old.example.com"
NEW = "https://www.example.com"


def entry(src, tgt):
    return Entry(parse_url(src), parse_url(tgt), src, tgt)


def run(argv, session):
    return main(argv, session_factory=lambda: session)


def check(src, tgt, heads, **kwargs):
    return verify_entry(entry(src, tgt), FakeSession(heads=heads), **kwargs)


def test_correct_301_is_ok():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": (301, NEW + "/a/")})
    assert (r.status, r.code) == ("ok", "ok")
    assert r.final_url == NEW + "/a/"
    assert [h.status for h in r.hops] == [301, 200]


def test_308_counts_as_permanent():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": (308, NEW + "/a/")})
    assert r.status == "ok"


def test_relative_location_is_resolved():
    r = check(NEW + "/old", NEW + "/new/", {NEW + "/old": (301, "/new/")})
    assert r.status == "ok"
    assert r.hops[0].location == NEW + "/new/"


def test_relative_expected_target_uses_request_host():
    r = check(NEW + "/old", "/new/", {NEW + "/old": (301, NEW + "/new/")})
    assert r.status == "ok"
    assert r.expected == NEW + "/new/"


def test_not_redirecting():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": 200})
    assert (r.status, r.code) == ("error", "not-redirecting")
    assert "200" in r.message


def test_missing_rule_404():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": 404})
    assert r.code == "not-redirecting"


def test_wrong_target():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": (301, NEW + "/")})
    assert (r.status, r.code) == ("error", "wrong-target")
    assert r.final_url == NEW + "/"


def test_trailing_slash_variant_is_warning():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": (301, NEW + "/a")})
    assert (r.status, r.code) == ("warning", "target-variant")


def test_temporary_redirect_is_warning():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": (302, NEW + "/a/")})
    assert (r.status, r.code) == ("warning", "temporary-redirect")


def test_live_chain_is_warning():
    heads = {OLD + "/a": (301, NEW + "/a"), NEW + "/a": (301, NEW + "/a/")}
    r = check(OLD + "/a", NEW + "/a/", heads)
    assert (r.status, r.code) == ("warning", "chain")
    assert len(r.hops) == 3


def test_destination_404_is_error():
    heads = {OLD + "/a": (301, NEW + "/a/"), NEW + "/a/": 404}
    r = check(OLD + "/a", NEW + "/a/", heads)
    assert (r.status, r.code) == ("error", "target-status")


def test_loop_detected():
    heads = {OLD + "/a": (301, OLD + "/b"), OLD + "/b": (301, OLD + "/a")}
    r = check(OLD + "/a", NEW + "/a/", heads)
    assert (r.status, r.code) == ("error", "loop")


def test_max_hops_bounds_requests():
    heads = {OLD + "/%d" % i: (301, OLD + "/%d" % (i + 1)) for i in range(50)}
    session = FakeSession(heads=heads)
    r = verify_entry(entry(OLD + "/0", NEW + "/x"), session, max_hops=3)
    assert (r.status, r.code) == ("error", "too-many-hops")
    assert len(session.calls) == 3


def test_non_http_location_is_error():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": (301, "javascript:alert(1)")})
    assert (r.status, r.code) == ("error", "bad-location")


def test_missing_location_header():
    session = FakeSession(heads={OLD + "/a": 301})
    r = verify_entry(entry(OLD + "/a", NEW + "/a/"), session)
    assert r.code == "missing-location"


def test_network_error_is_reported():
    r = check(OLD + "/a", NEW + "/a/", {OLD + "/a": requests.ConnectionError("boom")})
    assert (r.status, r.code) == ("error", "unreachable")
    assert "ConnectionError" in r.message


def test_bare_path_needs_base():
    r = check("/a", NEW + "/a/", {})
    assert r.code == "no-host"


def test_base_replaces_source_host():
    src = parse_url(OLD + "/a?x=1")
    assert request_url_for(src, "http://staging.example.com:8080") \
        == "http://staging.example.com:8080/a?x=1"
    assert request_url_for(parse_url("/a"), "staging.example.com") \
        == "https://staging.example.com/a"
    assert request_url_for(parse_url("/a"), None) is None


def test_invalid_base_rejected():
    with pytest.raises(VerifyInputError):
        verify([entry(OLD + "/a", NEW + "/")], FakeSession(), base="ftp://x")


def test_invalid_method_rejected():
    with pytest.raises(VerifyInputError):
        verify([entry(OLD + "/a", NEW + "/")], FakeSession(), method="POST")


class GetOnlySession(FakeSession):
    """HEAD is refused with 405; GET answers from ``gets``."""

    def __init__(self, gets):
        super().__init__()
        self.gets = gets

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url))
        return FakeResponse(b"", 405)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        assert kwargs.get("allow_redirects") is False
        status, loc = self.gets.get(url, (200, None))
        return FakeResponse(b"", status, headers={"Location": loc} if loc else {})


def test_head_405_falls_back_to_get():
    session = GetOnlySession({OLD + "/a": (301, NEW + "/a/")})
    r = verify_entry(entry(OLD + "/a", NEW + "/a/"), session)
    assert r.status == "ok"
    assert ("GET", OLD + "/a") in session.calls


def test_get_method_skips_head():
    session = GetOnlySession({OLD + "/a": (301, NEW + "/a/")})
    r = verify_entry(entry(OLD + "/a", NEW + "/a/"), session, method="GET")
    assert r.status == "ok"
    assert all(m == "GET" for m, _ in session.calls)


def test_verify_dedupes_sorts_and_limits():
    heads = {OLD + "/a": (301, NEW + "/a/"), OLD + "/b": 200, OLD + "/c": (302, NEW + "/c/")}
    entries = [entry(OLD + "/a", NEW + "/a/"), entry(OLD + "/a", NEW + "/other/"),
               entry(OLD + "/b", NEW + "/b/"), entry(OLD + "/c", NEW + "/c/")]
    results = verify(entries, FakeSession(heads=heads), rate=0)
    assert [r.status for r in results] == ["error", "warning", "ok"]
    assert summarize(results) == {"checked": 3, "ok": 1, "warnings": 1, "errors": 1,
                                  "by_code": {"not-redirecting": 1, "ok": 1,
                                              "temporary-redirect": 1}}
    assert len(verify(entries, FakeSession(heads=heads), rate=0, limit=1)) == 1


def test_verify_is_rate_limited():
    sleeps = []
    clock = iter([0.0, 0.0, 0.0, 0.0])
    limiter = RateLimiter(2, clock=lambda: next(clock), sleep=sleeps.append)
    heads = {OLD + "/a": (301, NEW + "/a/"), OLD + "/b": (301, NEW + "/b/")}
    verify([entry(OLD + "/a", NEW + "/a/"), entry(OLD + "/b", NEW + "/b/")],
           FakeSession(heads=heads), limiter=limiter)
    assert sleeps and all(s > 0 for s in sleeps)


def test_report_json_roundtrip():
    results = verify([entry(OLD + "/a", NEW + "/a/")],
                     FakeSession(heads={OLD + "/a": (301, NEW + "/a/")}), rate=0)
    data = json.loads(to_report(results, {"base": None}))
    assert data["summary"]["ok"] == 1
    assert data["results"][0]["hops"][0] == {"url": OLD + "/a", "status": 301,
                                             "location": NEW + "/a/"}


def test_load_redirects_from_map_report(write):
    path = write("report.json", json.dumps({"matches": [
        {"old": OLD + "/a", "new": NEW + "/a/"}, {"old": "", "new": NEW}, "junk"]}))
    entries = load_redirects(path)
    assert [(e.raw_source, e.raw_target) for e in entries] == [(OLD + "/a", NEW + "/a/")]


def test_load_redirects_rejects_bad_json(write):
    with pytest.raises(VerifyInputError):
        load_redirects(write("bad.json", "{nope"))
    with pytest.raises(VerifyInputError):
        load_redirects(write("list.json", "[]"))


def test_load_redirects_csv(write):
    path = write("r.csv", "source,target\n%s/a,%s/a/\n" % (OLD, NEW))
    assert len(load_redirects(path)) == 1


# --- CLI -------------------------------------------------------------------


def test_cli_verify_ok(write, tmp_path):
    path = write("r.csv", "source,target\n%s/a,%s/a/\n" % (OLD, NEW))
    out = tmp_path / "v.json"
    session = FakeSession(heads={OLD + "/a": (301, NEW + "/a/")})
    assert run(["verify", path, "--rate", "0", "--json", str(out)], session) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["checked"] == 1
    assert data["meta"]["method"] == "HEAD"


def test_cli_verify_errors_exit_1(write):
    path = write("r.csv", "%s/a,%s/a/\n" % (OLD, NEW))
    assert run(["verify", path, "--rate", "0"], FakeSession(heads={OLD + "/a": 200})) == 1


def test_cli_verify_warnings(write):
    path = write("r.csv", "%s/a,%s/a/\n" % (OLD, NEW))
    heads = {OLD + "/a": (302, NEW + "/a/")}
    assert run(["verify", path, "--rate", "0", "-q"], FakeSession(heads=heads)) == 0
    assert run(["verify", path, "--rate", "0", "--fail-on-warning", "-q"],
               FakeSession(heads=heads)) == 1


def test_cli_verify_base_for_staging(write):
    path = write("r.csv", "/a,%s/a/\n" % NEW)
    session = FakeSession(heads={"https://staging.example.com/a": (301, NEW + "/a/")})
    assert run(["verify", path, "--base", "https://staging.example.com", "--rate", "0"],
               session) == 0
    assert session.calls[0] == ("HEAD", "https://staging.example.com/a")


def test_cli_verify_bad_inputs(tmp_path, write):
    assert run(["verify", str(tmp_path / "missing.csv")], FakeSession()) == 2
    assert run(["verify", write("empty.csv", "source,target\n")], FakeSession()) == 2
    path = write("r.csv", "%s/a,%s/a/\n" % (OLD, NEW))
    assert run(["verify", path, "--base", "ftp://x"], FakeSession()) == 2


def test_cli_verify_hostile_location_is_printed_safely(write, capsys):
    path = write("r.csv", "%s/a,%s/a/\n" % (OLD, NEW))
    heads = {OLD + "/a": (301, NEW + "/[bold]x[/bold]")}
    assert run(["verify", path, "--rate", "0"], FakeSession(heads=heads)) == 1
    assert "[bold]x[/bold]" in capsys.readouterr().out


def test_cli_verify_map_report(tmp_path):
    examples = os.path.join(os.path.dirname(__file__), "..", "examples")
    out = tmp_path / "out"
    assert run(["map", "--old", os.path.join(examples, "old-sitemap.xml"),
                "--new", os.path.join(examples, "new-sitemap.xml"),
                "--out-dir", str(out), "--formats", "json", "-q"], FakeSession()) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    heads = {m["old"]: (301, m["new"]) for m in report["matches"]}
    assert run(["verify", str(out / "report.json"), "--rate", "0", "-q"],
               FakeSession(heads=heads)) == 0
