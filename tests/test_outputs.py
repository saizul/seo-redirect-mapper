import csv
import io
import json

from redirect_mapper import outputs
from redirect_mapper.matching import Match, MatchConfig, build_map
from redirect_mapper.urls import parse_url


def mk(old, new, method="exact-path", conf=1.0):
    return Match(parse_url(old), new, method, conf)


def test_nginx_map_has_both_slash_variants():
    text = outputs.to_nginx_map([mk("https://old.com/about/", "https://new.com/about-us/")])
    assert 'map $request_uri $redirect_target {' in text
    assert '"/about/" "https://new.com/about-us/";' in text
    assert '"/about" "https://new.com/about-us/";' in text
    assert 'default "";' in text
    assert "return 301 $redirect_target" in text


def test_nginx_map_without_trailing_slash_adds_slash_variant():
    text = outputs.to_nginx_map([mk("https://old.com/about", "https://new.com/a/")])
    assert '"/about" ' in text and '"/about/" ' in text


def test_nginx_map_query_source_is_exact_only():
    text = outputs.to_nginx_map([mk("https://old.com/?p=7", "https://new.com/post/")])
    assert '"/?p=7" "https://new.com/post/";' in text
    assert text.count("/?p=7") == 1


def test_nginx_map_dedupes_sources():
    text = outputs.to_nginx_map([mk("https://old.com/a", "https://new.com/1"),
                                 mk("https://old.com/a", "https://new.com/2")])
    assert "https://new.com/2" not in text


def test_nginx_escapes_dangerous_characters_in_target():
    text = outputs.to_nginx_map([mk("https://old.com/a", 'https://new.com/x";$uri{}')])
    body = text.split("default")[1]
    assert '$uri' not in body and "{" not in body.split("\n", 1)[1].replace("map $request_uri", "")


def test_nginx_return_blocks():
    text = outputs.to_nginx_return([mk("https://old.com/a", "https://new.com/b/"),
                                    mk("https://old.com/?p=3", "https://new.com/c/")])
    assert 'location = "/a" { return 301 "https://new.com/b/"; }' in text
    assert 'if ($request_uri = "/?p=3") { return 301 "https://new.com/c/"; }' in text


def test_apache_redirectmatch_default():
    text = outputs.to_apache([mk("https://old.com/about-us.html", "https://new.com/about/")])
    assert 'RedirectMatch 301 "^/about-us\\.html/?$" "https://new.com/about/"' in text


def test_apache_redirect_style_warns_about_prefix():
    text = outputs.to_apache([mk("https://old.com/a/", "https://new.com/b/")], style="redirect")
    assert 'Redirect 301 /a "https://new.com/b/"' in text
    assert "PREFIX" in text


def test_apache_query_source_uses_rewrite():
    text = outputs.to_apache([mk("https://old.com/index.php?p=5", "https://new.com/post/")])
    assert "RewriteCond %{QUERY_STRING} ^p=5$" in text
    assert "RewriteRule ^index\\.php$ https://new.com/post/? [R=301,L]" in text
    assert "RedirectMatch" not in text


def test_apache_root_source():
    text = outputs.to_apache([mk("https://old.com/", "https://new.com/")])
    assert '"^/$"' in text


def test_cloudflare_csv_columns_and_rows():
    text, skipped = outputs.to_cloudflare([mk("https://old.com/a", "https://new.com/b/")])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:3] == ["source_url", "target_url", "status_code"]
    assert rows[1][:3] == ["old.com/a", "https://new.com/b/", "301"]
    assert skipped == 0


def test_cloudflare_skips_query_and_hostless_sources():
    text, skipped = outputs.to_cloudflare([mk("https://old.com/?p=1", "https://new.com/x/"),
                                           mk("/bare", "https://new.com/y/")])
    assert skipped == 2
    assert len(list(csv.reader(io.StringIO(text)))) == 1


def test_wordpress_redirection_csv():
    text = outputs.to_wordpress_redirection([mk("https://old.com/a/", "https://new.com/b/")])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["source", "target", "regex", "code"]
    assert rows[1] == ["/a/", "https://new.com/b/", "0", "301"]


def test_report_structure_and_confidence_average():
    old = [parse_url("https://old.com/about"), parse_url("https://old.com/zzzzzzz")]
    new = [parse_url("https://new.com/about")]
    result = build_map(old, new, {}, MatchConfig(min_confidence=0.9))
    report = outputs.build_report(result, issues=[{"code": "x"}], meta={"v": 1})
    assert report["summary"]["old_urls"] == 2
    assert report["summary"]["matched"] == 1 and report["summary"]["unmatched"] == 1
    assert report["summary"]["average_confidence"] == 1.0
    assert report["issues"] == [{"code": "x"}] and report["meta"] == {"v": 1}
    assert json.loads(outputs.to_json(report))["matches"][0]["method"] == "exact-path"


def test_unmatched_csv_has_suggestions():
    old = [parse_url("https://old.com/abcdefg")]
    new = [parse_url("https://new.com/abcdefh"), parse_url("https://new.com/other")]
    result = build_map(old, new, {}, MatchConfig(min_confidence=0.99))
    rows = list(csv.reader(io.StringIO(outputs.unmatched_csv(result))))
    assert rows[0][0] == "old_url" and rows[1][0] == "https://old.com/abcdefg"
    assert len(rows[1]) >= 3


def test_empty_matches_produce_valid_files():
    assert "map $request_uri" in outputs.to_nginx_map([])
    assert outputs.to_apache([]).startswith("#")
    assert outputs.to_cloudflare([])[0].count("\n") == 1


def test_apache_matches_decoded_path_for_unicode_sources():
    text = outputs.to_apache([mk("https://old.com/%E0%A6%AC%E0%A6%BE%E0%A6%82%E0%A6%B2%E0%A6%BE/",
                                 "https://new.com/bangla/")])
    assert 'RedirectMatch 301 "^/\u09ac\u09be\u0982\u09b2\u09be/?$"' in text
    assert "%E0" not in text.split("RedirectMatch")[1].split('" "')[0]


def test_apache_decodes_spaces_in_source():
    text = outputs.to_apache([mk("https://old.com/my%20page", "https://new.com/p/")])
    assert "my\\ page" in text
