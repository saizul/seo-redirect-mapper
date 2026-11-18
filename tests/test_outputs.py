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


def test_netlify_one_rule_per_source():
    text, skipped = outputs.to_netlify([mk("https://old.com/about/", "https://new.com/about-us/"),
                                        mk("https://old.com/about", "https://new.com/other/"),
                                        mk("/", "https://new.com/home/")])
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert lines == ["/about  https://new.com/about-us/  301", "/  https://new.com/home/  301"]
    assert skipped == 0


def test_netlify_skips_query_and_placeholder_like_paths():
    text, skipped = outputs.to_netlify([mk("https://old.com/?p=7", "https://new.com/a/"),
                                        mk("https://old.com/a:b", "https://new.com/b/"),
                                        mk("https://old.com/x*y", "https://new.com/c/")])
    assert skipped == 3
    assert [line for line in text.splitlines() if not line.startswith("#")] == []


def test_netlify_target_is_config_safe():
    text, _ = outputs.to_netlify([mk("https://old.com/a", "https://new.com/x y")])
    assert "/a  https://new.com/x%20y  301" in text


def test_vercel_redirects_json():
    text, count = outputs.to_vercel([mk("https://old.com/about/", "https://new.com/about-us/")])
    data = json.loads(text)
    assert count == 2
    assert data["redirects"] == [
        {"source": "/about/", "destination": "https://new.com/about-us/", "permanent": True},
        {"source": "/about", "destination": "https://new.com/about-us/", "permanent": True},
    ]


def test_vercel_query_sources_use_has_conditions():
    text, count = outputs.to_vercel([mk("https://old.com/index.php?p=7&page_id=", "https://new.com/a/")])
    (rule,) = json.loads(text)["redirects"]
    assert count == 1
    assert rule["source"] == "/index.php"
    assert rule["has"] == [{"type": "query", "key": "p", "value": "7"},
                           {"type": "query", "key": "page_id"}]


def test_vercel_escapes_path_to_regexp_syntax():
    text, _ = outputs.to_vercel([mk("https://old.com/a:b(1)+c", "https://new.com/x/")])
    sources = [rule["source"] for rule in json.loads(text)["redirects"]]
    assert sources[0] == "/a\\:b\\(1\\)\\+c"


def test_vercel_dedupes_and_empty():
    text, count = outputs.to_vercel([mk("https://old.com/a", "https://new.com/1"),
                                     mk("https://old.com/a", "https://new.com/2")])
    assert "https://new.com/2" not in text and count == 2
    assert json.loads(outputs.to_vercel([])[0]) == {"redirects": []}


def test_caddy_redir_directives():
    text, skipped = outputs.to_caddy([mk("https://old.com/about", "https://new.com/about-us/"),
                                      mk("https://old.com/?p=7&x=1", "https://new.com/post/")])
    assert skipped == 0
    assert "redir /about https://new.com/about-us/ 301" in text
    assert "redir /about/ https://new.com/about-us/ 301" in text
    assert "@redirect_query_1 {\n    path /\n    query p=7 x=1\n}\n" in text
    assert "redir @redirect_query_1 https://new.com/post/ 301" in text


def test_caddy_skips_wildcards_and_quotes_braces():
    text, skipped = outputs.to_caddy([mk("https://old.com/a*b", "https://new.com/x/")])
    assert skipped == 1 and "redir" not in text.replace("redirect-mapper", "").replace(
        "redirects.caddy", "")
    assert outputs._caddy_token('a"b c') == '"a\\"b c"'


def test_new_formats_are_registered():
    for fmt, name in (("netlify", "_redirects"), ("vercel", "vercel.json"),
                      ("caddy", "redirects.caddy")):
        assert fmt in outputs.FORMATS and outputs.FILENAMES[fmt] == name
