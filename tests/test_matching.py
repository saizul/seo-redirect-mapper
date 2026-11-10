import pytest

from redirect_mapper import matching, similarity
from redirect_mapper.matching import MatchConfig, build_map
from redirect_mapper.urls import parse_url


def recs(*urls, **kw):
    out = []
    for u in urls:
        r = parse_url(u, base=kw.get("base"))
        out.append(r)
    return out


def run(old, new, overrides=None, **cfg):
    return build_map(recs(*old), recs(*new), overrides or {}, MatchConfig(**cfg))


def only(result):
    assert len(result.matches) == 1, result
    return result.matches[0]


def test_exact_path_on_different_host_redirects(backend):
    m = only(run(["https://old.com/about/"], ["https://new.com/about/"]))
    assert m.method == matching.EXACT and m.confidence == 1.0
    assert m.target == "https://new.com/about/"


def test_same_host_same_path_is_unchanged(backend):
    r = run(["https://same.com/about/"], ["https://same.com/about/"])
    assert not r.matches and len(r.unchanged) == 1


def test_bare_paths_equal_are_unchanged():
    r = run(["/about"], ["/about"])
    assert len(r.unchanged) == 1


def test_trailing_slash_and_case_difference(backend):
    m = only(run(["https://old.com/About-Us"], ["https://new.com/about-us/"]))
    assert m.method == matching.EXACT_LOOSE and m.confidence == pytest.approx(0.98)


def test_same_host_trailing_slash_difference_is_canonical_redirect():
    m = only(run(["https://x.com/about"], ["https://x.com/about/"]))
    assert m.method == matching.EXACT_LOOSE


def test_normalized_path_strips_dates_and_extensions(backend):
    m = only(run(["https://old.com/2019/05/12/speed-guide.html"],
                 ["https://new.com/speed-guide/"]))
    assert m.method in (matching.NORM_PATH, matching.SLUG)
    assert m.confidence >= 0.9


def test_normalized_path_strips_ids():
    m = only(run(["https://old.com/blog/123456-seo-audit"], ["https://new.com/blog/seo-audit/"]))
    assert m.method == matching.NORM_PATH and m.confidence == 0.95


def test_slug_match_ignores_directory_change():
    m = only(run(["https://old.com/blog/technical-seo-checklist"],
                 ["https://new.com/guides/technical-seo-checklist/"]))
    assert m.method == matching.SLUG and m.confidence == 0.9


def test_slug_ambiguity_lowers_confidence_and_prefers_overlap():
    m = only(run(["https://old.com/blog/seo/audit"],
                 ["https://new.com/seo/audit/", "https://new.com/wordpress/audit/"]))
    assert m.method == matching.NORM_PATH or m.method == matching.SLUG
    assert m.target == "https://new.com/seo/audit/"


def test_ambiguous_slug_flags_note():
    m = only(run(["https://old.com/x/audit"],
                 ["https://new.com/seo/audit/", "https://new.com/wp/audit/"]))
    assert "ambiguous" in m.note and m.confidence < 0.9


def test_bangla_slug_matches_latin_slug():
    m = only(run(["https://old.com/%E0%A6%AC%E0%A6%BE%E0%A6%82%E0%A6%B2%E0%A6%BE/"],
                 ["https://new.com/bangla/"]))
    assert m.target == "https://new.com/bangla/"
    assert m.confidence >= 0.9


def test_manual_override_wins_over_everything():
    r = run(["https://old.com/about"], ["https://new.com/about"],
            {"/about": "https://new.com/company/"})
    m = only(r)
    assert m.method == matching.OVERRIDE and m.confidence == 1.0
    assert m.target == "https://new.com/company/"


def test_override_matches_by_loose_key_and_full_url():
    r = run(["https://old.com/Page/"], ["https://new.com/z/"], {"/page": "/z/"},
            new_base="https://new.com")
    assert only(r).target == "https://new.com/z/"
    r = run(["https://old.com/q"], ["https://new.com/z/"], {"https://old.com/q": "/z/"},
            new_base="https://new.com")
    assert only(r).method == matching.OVERRIDE


def test_title_similarity_match():
    old = recs("https://old.com/p1")
    new = recs("https://new.com/zz-top-9", "https://new.com/different-thing")
    old[0].title = "How to Speed Up WordPress | Old Site"
    new[0].title = "How to speed up WordPress - New Site"
    new[1].title = "Contact us"
    r = build_map(old, new, {}, MatchConfig())
    m = only(r)
    assert m.method == matching.TITLE and m.target == "https://new.com/zz-top-9"
    assert 0.7 < m.confidence <= 0.9


def test_title_below_threshold_is_not_used():
    old = recs("https://old.com/qqqq")
    new = recs("https://new.com/wwww")
    old[0].title = "Completely different heading"
    new[0].title = "Another unrelated words here"
    assert not build_map(old, new, {}, MatchConfig()).matches


def test_fuzzy_path_match(backend):
    r = run(["https://old.com/wordpress-seo-guides"],
            ["https://new.com/wordpress-seo-guide/", "https://new.com/contact/"],
            min_confidence=0.5)
    m = only(r)
    assert m.method == matching.FUZZY
    assert m.target == "https://new.com/wordpress-seo-guide/"
    assert m.confidence >= 0.5


def test_fuzzy_respects_min_confidence():
    r = run(["https://old.com/wordpress-seo-guides"], ["https://new.com/wordpress-seo-guide/"],
            min_confidence=0.99)
    assert len(r.unmatched) == 1 and not r.matches


def test_unmatched_has_suggestions():
    r = run(["https://old.com/totally-unrelated-thing"],
            ["https://new.com/contact/", "https://new.com/about/"])
    assert len(r.unmatched) == 1
    u = r.unmatched[0]
    assert u.old.path == "/totally-unrelated-thing"
    assert isinstance(u.suggestions, list)


def test_fallback_used_for_unmatched():
    r = run(["https://old.com/zzzzzz"], ["https://new.com/contact/"],
            fallback="https://new.com/", min_confidence=0.9)
    m = only(r)
    assert m.method == matching.FALLBACK and m.confidence == 0.2


def test_parent_path_heuristic():
    r = run(["https://old.com/seo/old-deep-page"],
            ["https://new.com/seo/", "https://new.com/contact/"], min_confidence=0.6)
    m = only(r)
    assert m.method == matching.PARENT and m.target == "https://new.com/seo/"
    assert m.confidence == pytest.approx(0.65)


def test_parent_not_used_when_min_confidence_higher():
    r = run(["https://old.com/seo/old-deep-page"], ["https://new.com/seo/"], min_confidence=0.9)
    assert r.unmatched


def test_query_urls_do_not_exact_match_plain_path():
    r = run(["https://old.com/?p=123"], ["https://new.com/"])
    assert not (r.matches and r.matches[0].method == matching.EXACT)


def test_many_old_can_map_to_one_new():
    r = run(["https://old.com/a/contact", "https://old.com/b/contact"],
            ["https://new.com/contact/"])
    assert len(r.matches) == 2
    assert {m.target for m in r.matches} == {"https://new.com/contact/"}


def test_by_method_counts():
    r = run(["https://old.com/about", "https://old.com/x/contact"],
            ["https://new.com/about", "https://new.com/contact/"])
    counts = r.by_method()
    assert counts[matching.EXACT] == 1 and counts[matching.SLUG] == 1


def test_match_to_dict_rounds_confidence():
    m = only(run(["https://old.com/about"], ["https://new.com/about"]))
    d = m.to_dict()
    assert d["old"] == "https://old.com/about" and d["confidence"] == 1.0


def test_pipeline_is_identical_under_both_backends():
    old = ["https://old.com/wordpress-seo-guides", "https://old.com/blog/foo-bar"]
    new = ["https://new.com/wordpress-seo-guide/", "https://new.com/foo-bar/"]
    results = []
    for name in ("difflib", "rapidfuzz"):
        if name == "rapidfuzz" and not similarity.HAS_RAPIDFUZZ:
            continue
        similarity.set_backend(name)
        results.append([(m.target, m.method) for m in run(old, new, min_confidence=0.5).matches])
    similarity.set_backend("rapidfuzz" if similarity.HAS_RAPIDFUZZ else "difflib")
    assert all(r == results[0] for r in results)


def test_empty_new_list_gives_unmatched():
    r = build_map(recs("https://old.com/a"), [], {}, MatchConfig())
    assert len(r.unmatched) == 1
