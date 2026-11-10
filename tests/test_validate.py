import pytest

from conftest import FakeSession
from redirect_mapper import validate as v
from redirect_mapper.http import RateLimiter
from redirect_mapper.urls import parse_url


def E(src, tgt):
    return v.Entry(parse_url(src), parse_url(tgt), src, tgt)


def codes(issues):
    return sorted(i.code for i in issues)


def test_no_issues_for_clean_map():
    entries = [E("https://old.com/a", "https://new.com/a"), E("https://old.com/b", "https://new.com/b")]
    assert v.validate(entries) == []


def test_detects_chain_and_suggests_final():
    entries = [E("/a", "/b"), E("/b", "/c")]
    issues = v.find_chains_and_loops(entries)
    assert codes(issues) == ["chain"]
    assert issues[0].source == "/a" and "/c" in issues[0].message


def test_long_chain_counts_hops():
    entries = [E("/a", "/b"), E("/b", "/c"), E("/c", "/d")]
    chains = [i for i in v.find_chains_and_loops(entries) if i.code == "chain"]
    assert len(chains) == 2
    assert "3 hops" in chains[0].message


def test_detects_two_node_loop_once():
    issues = v.find_chains_and_loops([E("/a", "/b"), E("/b", "/a")])
    assert codes(issues) == ["loop"]
    assert issues[0].severity == v.ERROR


def test_detects_self_redirect():
    issues = v.find_chains_and_loops([E("https://x.com/a", "https://x.com/a/")])
    assert codes(issues) == ["self-redirect"]


def test_same_path_different_host_is_not_self_redirect():
    assert v.find_chains_and_loops([E("https://old.com/a", "https://new.com/a")]) == []


def test_three_node_loop():
    issues = v.find_chains_and_loops([E("/a", "/b"), E("/b", "/c"), E("/c", "/a")])
    assert [i.code for i in issues] == ["loop"]


def test_chain_across_hosts_only_when_hosts_match():
    entries = [E("https://old.com/a", "https://new.com/b"), E("https://other.com/b", "https://new.com/c")]
    assert v.find_chains_and_loops(entries) == []
    entries = [E("https://old.com/a", "https://new.com/b"), E("https://new.com/b", "https://new.com/c")]
    assert codes(v.find_chains_and_loops(entries)) == ["chain"]


def test_duplicate_source_conflicting_targets_is_error():
    issues = v.find_duplicates([E("/a", "/x"), E("/a/", "/y")])
    assert len(issues) == 1 and issues[0].severity == v.ERROR
    assert issues[0].detail == ["/x", "/y"]


def test_duplicate_source_same_target_is_warning():
    issues = v.find_duplicates([E("/a", "/x"), E("/A", "/x")])
    assert issues[0].severity == v.WARNING


def test_query_sources_are_distinct():
    assert v.find_duplicates([E("/?p=1", "/x"), E("/?p=2", "/y")]) == []


def test_unknown_targets_flagged():
    known = [parse_url("https://new.com/a")]
    issues = v.find_unknown_targets([E("/o", "https://new.com/a"), E("/p", "https://new.com/zzz")],
                                    known)
    assert [i.source for i in issues] == ["/p"]


def test_live_sources_flagged():
    known = [parse_url("https://new.com/live")]
    issues = v.find_live_sources([E("https://new.com/live", "https://new.com/x"),
                                  E("https://old.com/gone", "https://new.com/y")], known)
    assert [i.source for i in issues] == ["https://new.com/live"]


def test_flatten_resolves_chains():
    final = v.flatten([E("/a", "/b"), E("/b", "/c"), E("/c", "/d")])
    assert final == {"/a": "/d", "/b": "/d"}


def test_flatten_ignores_loops():
    assert v.flatten([E("/a", "/b"), E("/b", "/a")]) == {}


def test_check_targets_reports_404_and_redirects():
    session = FakeSession(heads={
        "https://new.com/gone": 404,
        "https://new.com/moved": (301, "https://new.com/elsewhere"),
        "https://new.com/boom": 500,
    })
    entries = [E("/1", "https://new.com/ok"), E("/2", "https://new.com/gone"),
               E("/3", "https://new.com/moved"), E("/4", "https://new.com/boom")]
    issues = v.check_targets(entries, session, rate=0)
    assert codes(issues) == ["target-redirects", "target-status", "target-status"]
    assert any("elsewhere" in i.message for i in issues)


def test_check_targets_dedupes_requests():
    session = FakeSession()
    entries = [E("/1", "https://new.com/same"), E("/2", "https://new.com/same")]
    v.check_targets(entries, session, rate=0)
    assert session.calls == [("HEAD", "https://new.com/same")]


def test_check_targets_handles_network_errors():
    session = FakeSession(heads={"https://new.com/x": ConnectionError("down")})
    issues = v.check_targets([E("/1", "https://new.com/x")], session, rate=0)
    assert codes(issues) == ["target-unreachable"]


def test_check_targets_rate_limited():
    slept = []
    now = [0.0]
    limiter = RateLimiter(4, clock=lambda: now[0],
                          sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    entries = [E("/%d" % i, "https://new.com/%d" % i) for i in range(4)]
    v.check_targets(entries, FakeSession(), limiter=limiter)
    assert len(slept) == 3 and slept[0] == pytest.approx(0.25)


def test_check_targets_skips_hostless_targets():
    session = FakeSession()
    v.check_targets([E("/a", "/b")], session, rate=0)
    assert session.calls == []


def test_validate_sorts_errors_first_and_has_errors():
    entries = [E("/a", "/b"), E("/b", "/a"), E("/c", "/x"), E("/c", "/x")]
    issues = v.validate(entries)
    assert issues[0].severity == v.ERROR
    assert v.has_errors(issues)
    assert not v.has_errors([i for i in issues if i.severity == v.WARNING])


def test_validate_runs_head_checks_only_when_requested():
    session = FakeSession(heads={"https://new.com/x": 404})
    entries = [E("https://old.com/a", "https://new.com/x")]
    assert v.validate(entries, session=session, check=False) == []
    assert codes(v.validate(entries, session=session, check=True, rate=0)) == ["target-status"]


def test_load_redirect_csv_with_and_without_header(write):
    path = write("r.csv", "source,target\n/a,/b\n/c,https://x.com/d\n")
    assert [(e.raw_source, e.raw_target) for e in v.load_redirect_csv(path)] == [
        ("/a", "/b"), ("/c", "https://x.com/d")]
    path = write("r2.csv", "/a,/b\n")
    assert len(v.load_redirect_csv(path)) == 1


def test_issue_to_dict_omits_empty_detail():
    d = v.Issue("error", "x", "/a", "msg").to_dict()
    assert "detail" not in d and d["code"] == "x"
