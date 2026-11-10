import csv
import json
import os
import re

import pytest

from conftest import FakeSession, sitemap_xml
from redirect_mapper.cli import main

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def run(argv, session=None):
    return main(argv, session_factory=lambda: session or FakeSession())


@pytest.fixture
def example_args(tmp_path):
    return ["map",
            "--old", os.path.join(EXAMPLES, "old-sitemap.xml"),
            "--new", os.path.join(EXAMPLES, "new-sitemap.xml"),
            "--overrides", os.path.join(EXAMPLES, "overrides.csv"),
            "--out-dir", str(tmp_path / "out")]


def test_map_writes_every_format(example_args, tmp_path):
    assert run(example_args) == 0
    out = tmp_path / "out"
    for name in ["nginx-redirect-map.conf", "nginx-redirects.conf", "redirects.htaccess",
                 "cloudflare-bulk-redirects.csv", "wordpress-redirection.csv", "report.json"]:
        assert (out / name).is_file(), name


def test_map_example_results(example_args, tmp_path):
    run(example_args)
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    by_old = {m["old"]: m for m in report["matches"]}
    assert by_old["https://old.example.com/about-us.html"]["new"] == "https://www.example.com/about-us/"
    assert by_old["https://old.example.com/2019/05/12/wordpress-speed-guide/"]["new"] \
        == "https://www.example.com/wordpress-speed-guide/"
    assert by_old["https://old.example.com/old-promo-page"]["method"] == "override"
    assert by_old["https://old.example.com/blog/123-technical-seo-checklist"]["new"] \
        == "https://www.example.com/technical-seo-checklist/"
    assert report["summary"]["matched"] >= 7
    assert report["meta"]["tool"] == "redirect-mapper"


def test_map_bangla_example_goes_to_latin_slug(example_args, tmp_path):
    run(example_args)
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    bangla = [m for m in report["matches"] if "%E0%A6%AC" in m["old"]]
    assert bangla and bangla[0]["new"] == "https://www.example.com/bangla/"


def test_map_formats_selection(example_args, tmp_path):
    assert run(example_args + ["--formats", "nginx-map,json"]) == 0
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == ["nginx-redirect-map.conf", "report.json", "unmatched.csv"]


def test_map_rejects_unknown_format(example_args):
    assert run(example_args + ["--formats", "iis"]) == 2


def test_map_missing_file_is_usage_error(tmp_path):
    code = run(["map", "--old", str(tmp_path / "none.xml"), "--new", str(tmp_path / "n2.xml"),
                "--out-dir", str(tmp_path / "o")])
    assert code == 2


def test_map_empty_list_is_error(tmp_path, write):
    old = write("o.txt", "")
    new = write("n.txt", "/a\n")
    assert run(["map", "--old", old, "--new", new, "--out-dir", str(tmp_path / "o")]) == 2


def test_map_unmatched_csv_written(tmp_path, write):
    old = write("o.txt", "https://old.com/qwertyuiop\n")
    new = write("n.txt", "https://new.com/contact/\n")
    assert run(["map", "--old", old, "--new", new, "--out-dir", str(tmp_path / "o")]) == 0
    rows = list(csv.reader((tmp_path / "o" / "unmatched.csv").open(encoding="utf-8")))
    assert rows[1][0] == "https://old.com/qwertyuiop"


def test_map_fallback_flag(tmp_path, write):
    old = write("o.txt", "https://old.com/qwertyuiop\n")
    new = write("n.txt", "https://new.com/contact/\n")
    assert run(["map", "--old", old, "--new", new, "--fallback", "https://new.com/",
                "--out-dir", str(tmp_path / "o")]) == 0
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    assert report["matches"][0]["method"] == "fallback"


def test_map_strict_fails_on_validation_errors(tmp_path, write):
    old = write("o.txt", "/a\n/b\n")
    new = write("n.txt", "https://new.com/x\n")
    ov = write("ov.csv", "old,new\n/a,/b\n/b,/a\n")
    args = ["map", "--old", old, "--new", new, "--overrides", ov, "--out-dir", str(tmp_path / "o")]
    assert run(args) == 0
    assert run(args + ["--strict"]) == 1
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    assert any(i["code"] == "loop" for i in report["issues"])


def test_map_flatten_rewrites_chain_targets(tmp_path, write):
    old = write("o.txt", "https://old.com/a\nhttps://old.com/b\n")
    new = write("n.txt", "https://new.com/final\n")
    ov = write("ov.csv", "old,new\nhttps://old.com/a,https://old.com/b\nhttps://old.com/b,https://new.com/final\n")
    assert run(["map", "--old", old, "--new", new, "--overrides", ov, "--flatten",
                "--out-dir", str(tmp_path / "o")]) == 0
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    targets = {m["old"]: m["new"] for m in report["matches"]}
    assert targets["https://old.com/a"] == "https://new.com/final"
    assert not any(i["code"] == "chain" for i in report["issues"])


def test_map_check_targets_uses_head(tmp_path, write):
    old = write("o.txt", "https://old.com/a\n")
    new = write("n.txt", "https://new.com/a\n")
    session = FakeSession(heads={"https://new.com/a": 404})
    code = run(["map", "--old", old, "--new", new, "--check-targets", "--check-rate", "0",
                "--strict", "--out-dir", str(tmp_path / "o")], session)
    assert code == 1
    assert ("HEAD", "https://new.com/a") in session.calls


def test_map_fetch_titles_enables_title_match(tmp_path, write):
    old = write("o.txt", "https://old.com/p1\n")
    new = write("n.txt", "https://new.com/zz9\n")
    session = FakeSession(pages={
        "https://old.com/p1": "<title>Ultimate Guide To Fast WordPress Hosting</title>",
        "https://new.com/zz9": "<title>Ultimate guide to fast WordPress hosting</title>",
    })
    assert run(["map", "--old", old, "--new", new, "--fetch-titles", "--fetch-rate", "0",
                "--out-dir", str(tmp_path / "o")], session) == 0
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    assert report["matches"][0]["method"] == "title"


def test_map_from_remote_sitemaps(tmp_path):
    session = FakeSession(pages={
        "https://old.com/sitemap.xml": sitemap_xml(["https://old.com/a/", "https://old.com/b/"]),
        "https://new.com/sitemap.xml": sitemap_xml(["https://new.com/a/", "https://new.com/b/"]),
    })
    assert run(["map", "--old", "https://old.com/sitemap.xml", "--new",
                "https://new.com/sitemap.xml", "--out-dir", str(tmp_path / "o")], session) == 0
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    assert report["summary"]["matched"] == 2


def test_map_crawl_export_filters_non_200_new_urls(tmp_path, write):
    old = write("o.txt", "https://old.com/a\n")
    new = write("n.csv", "Address,Status Code\nhttps://new.com/a,404\nhttps://new.com/b,200\n")
    run(["map", "--old", old, "--new", new, "--out-dir", str(tmp_path / "o")])
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    assert all(m["new"] != "https://new.com/a" for m in report["matches"])


def test_map_apache_style_option(example_args, tmp_path):
    run(example_args + ["--apache-style", "redirect"])
    text = (tmp_path / "out" / "redirects.htaccess").read_text()
    assert "Redirect 301 " in text and "RedirectMatch" not in text


def test_map_no_rapidfuzz_flag_uses_difflib(example_args, tmp_path):
    from redirect_mapper import similarity
    previous = similarity.backend()
    try:
        assert run(example_args + ["--no-rapidfuzz"]) == 0
        report = json.loads((tmp_path / "out" / "report.json").read_text())
        assert report["meta"]["similarity_backend"] == "difflib"
    finally:
        similarity.set_backend(previous)


def test_validate_command(tmp_path, write):
    path = write("r.csv", "source,target\n/a,/b\n/b,/a\n")
    assert run(["validate", path, "--json", str(tmp_path / "i.json")]) == 1
    issues = json.loads((tmp_path / "i.json").read_text())
    assert issues[0]["code"] == "loop"


def test_validate_command_clean(write):
    path = write("r.csv", "source,target\n/a,/b\n")
    assert run(["validate", path]) == 0


def test_validate_missing_file(tmp_path):
    assert run(["validate", str(tmp_path / "nope.csv")]) == 2


def test_validate_with_head_checks(write):
    path = write("r.csv", "source,target\n/a,https://new.com/gone\n")
    session = FakeSession(heads={"https://new.com/gone": 404})
    assert run(["validate", path, "--check-targets", "--check-rate", "0"], session) == 1


def test_inspect_lists_urls(write, capsys):
    path = write("u.txt", "https://a.com/1\nhttps://a.com/2\n")
    assert run(["inspect", path]) == 0
    out = capsys.readouterr().out
    assert "2 unique URL" in out and "https://a.com/1" in out


def test_cli_requires_subcommand():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert re.search(r"redirect-mapper \d+\.\d+\.\d+", capsys.readouterr().out)


def test_generated_nginx_map_is_syntactically_balanced(example_args, tmp_path):
    run(example_args)
    text = (tmp_path / "out" / "nginx-redirect-map.conf").read_text()
    body = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert body[0].endswith("{") and body[-1] == "}"
    assert all(ln.rstrip().endswith(";") for ln in body[2:-1])


def test_override_relative_target_gets_inferred_new_host(example_args, tmp_path):
    run(example_args)
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    promo = [m for m in report["matches"] if m["old"].endswith("/old-promo-page")][0]
    assert promo["new"] == "https://www.example.com/offers/"
