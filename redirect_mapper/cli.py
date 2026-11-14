"""Command line interface: ``redirect-mapper map|validate|verify|inspect``."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, List, Optional

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__, similarity
from .http import make_session
from .loaders import LoadError, load_many, load_overrides
from .matching import MapResult, MatchConfig, build_map
from .outputs import (
    FILENAMES,
    FORMATS,
    build_report,
    to_apache,
    to_cloudflare,
    to_json,
    to_nginx_map,
    to_nginx_return,
    to_wordpress_redirection,
    unmatched_csv,
)
from .titles import fetch_titles
from .validate import (
    Entry,
    Issue,
    entries_from_matches,
    flatten,
    has_errors,
    load_redirect_csv,
    validate,
)
from .verify import OK, VerifyInputError, load_redirects, summarize, to_report, verify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redirect-mapper",
        description="Build and validate 301 redirect maps for site migrations.",
    )
    parser.add_argument("--version", action="version", version="redirect-mapper " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("map", help="match old URLs to new URLs and write redirect files")
    m.add_argument("--old", nargs="+", required=True, metavar="SOURCE",
                   help="old URL list(s): sitemap (url/file, .xml/.gz/index), CSV, or text file")
    m.add_argument("--new", nargs="+", required=True, metavar="SOURCE", help="new URL list(s)")
    m.add_argument("--old-format", default="auto", choices=["auto", "sitemap", "csv", "txt"])
    m.add_argument("--new-format", default="auto", choices=["auto", "sitemap", "csv", "txt"])
    m.add_argument("--old-column", help="CSV column holding old URLs")
    m.add_argument("--new-column", help="CSV column holding new URLs")
    m.add_argument("--old-base", help="host for bare old paths, e.g. https://old.example.com")
    m.add_argument("--new-base", help="host for bare new paths, e.g. https://www.example.com")
    m.add_argument("--overrides", metavar="CSV", help="manual overrides CSV (old,new)")
    m.add_argument("--out-dir", default="redirects", help="output directory (default: redirects)")
    m.add_argument("--formats", default=",".join(FORMATS),
                   help="comma list of: %s (default: all)" % ", ".join(FORMATS))
    m.add_argument("--min-confidence", type=float, default=0.6,
                   help="minimum confidence for fuzzy/heuristic matches (default 0.6)")
    m.add_argument("--title-threshold", type=float, default=0.85,
                   help="title similarity needed for a title match (default 0.85)")
    m.add_argument("--fetch-titles", action="store_true",
                   help="fetch <title> of URLs lacking one (rate limited)")
    m.add_argument("--fetch-rate", type=float, default=2.0, help="title fetches per second")
    m.add_argument("--fallback", help="send unmatched URLs here (low confidence 0.2)")
    m.add_argument("--apache-style", choices=["match", "redirect"], default="match")
    m.add_argument("--flatten", action="store_true",
                   help="rewrite chained redirects to point to the final destination")
    m.add_argument("--check-targets", action="store_true",
                   help="HEAD-check every redirect target (rate limited)")
    m.add_argument("--check-rate", type=float, default=5.0, help="HEAD requests per second")
    m.add_argument("--strict", action="store_true", help="exit 1 if validation finds errors")
    m.add_argument("--no-rapidfuzz", action="store_true",
                   help="use difflib even if rapidfuzz is installed")
    m.add_argument("-q", "--quiet", action="store_true")

    v = sub.add_parser("validate", help="validate an existing redirect CSV (source,target)")
    v.add_argument("redirects", help="CSV with source,target columns")
    v.add_argument("--new", nargs="*", default=[], metavar="SOURCE",
                   help="new URL list(s) to check targets against")
    v.add_argument("--check-targets", action="store_true")
    v.add_argument("--check-rate", type=float, default=5.0)
    v.add_argument("--json", metavar="FILE", help="write issues as JSON")
    v.add_argument("-q", "--quiet", action="store_true")

    r = sub.add_parser("verify", help="request old URLs on the live site and check the redirects")
    r.add_argument("redirects",
                   help="source,target CSV or the report.json written by `map`")
    r.add_argument("--base", metavar="URL",
                   help="request sources on this scheme+host instead (e.g. a staging server); "
                        "required for bare-path sources")
    r.add_argument("--method", choices=["HEAD", "GET"], default="HEAD",
                   help="request method (default HEAD; falls back to GET on 405/501)")
    r.add_argument("--rate", type=float, default=5.0, help="requests per second (default 5)")
    r.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    r.add_argument("--max-hops", type=int, default=5,
                   help="follow at most this many redirects per source (default 5)")
    r.add_argument("--limit", type=int, help="only check the first N distinct sources")
    r.add_argument("--json", metavar="FILE", help="write every result (with hops) as JSON")
    r.add_argument("--fail-on-warning", action="store_true",
                   help="exit 1 on warnings (chains, 302s, slash variants) as well as errors")
    r.add_argument("-q", "--quiet", action="store_true")

    i = sub.add_parser("inspect", help="show what a URL source contains")
    i.add_argument("sources", nargs="+")
    i.add_argument("--format", default="auto", choices=["auto", "sitemap", "csv", "txt"])
    i.add_argument("--column")
    i.add_argument("--limit", type=int, default=10)
    return parser


def _print_issues(console: Console, issues: List[Issue], limit: int = 25,
                  more: str = "see report.json") -> None:
    if not issues:
        console.print("[green]Validation: no issues found[/green]")
        return
    table = Table(title="Validation issues (%d)" % len(issues))
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Source", overflow="fold")
    table.add_column("Message", overflow="fold")
    for issue in issues[:limit]:
        colour = "red" if issue.severity == "error" else "yellow"
        table.add_row("[%s]%s[/%s]" % (colour, issue.severity, colour), issue.code,
                      escape(issue.source), escape(issue.message))
    console.print(table)
    if len(issues) > limit:
        console.print("... and %d more (%s)" % (len(issues) - limit, more))


def _print_summary(console: Console, result: MapResult) -> None:
    table = Table(title="Redirect map summary")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    total = len(result.matches) + len(result.unmatched) + len(result.unchanged)
    table.add_row("Old URLs", str(total))
    table.add_row("Matched", str(len(result.matches)))
    table.add_row("Unmatched", str(len(result.unmatched)))
    table.add_row("Unchanged (same path)", str(len(result.unchanged)))
    for method, count in sorted(result.by_method().items()):
        table.add_row("  via " + method, str(count))
    console.print(table)


def _apply_flatten(result: MapResult, entries: List[Entry]) -> int:
    final = flatten(entries)
    changed = 0
    for m in result.matches:
        if m.old.url in final and final[m.old.url] != m.target:
            m.target = final[m.old.url]
            m.note = (m.note + "; flattened").strip("; ")
            changed += 1
    return changed


def cmd_map(args: argparse.Namespace, console: Console,
            session_factory: Callable[[], Any]) -> int:
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    bad = [f for f in formats if f not in FORMATS]
    if bad:
        console.print("[red]Unknown format(s): %s[/red]" % ", ".join(bad))
        return 2
    if args.no_rapidfuzz:
        similarity.set_backend("difflib")
    session = session_factory()
    try:
        old = load_many(args.old, fmt=args.old_format, session=session, base=args.old_base,
                        url_column=args.old_column)
        new = load_many(args.new, fmt=args.new_format, session=session, base=args.new_base,
                        url_column=args.new_column, only_ok=True)
        overrides = load_overrides(args.overrides) if args.overrides else {}
    except LoadError as exc:
        console.print("[red]error:[/red] %s" % exc)
        return 2
    if not old or not new:
        console.print("[red]error:[/red] %s list is empty" % ("old" if not old else "new"))
        return 2
    if args.fetch_titles:
        n = fetch_titles(old + new, session, rate=args.fetch_rate)
        if not args.quiet:
            console.print("Fetched %d title(s)" % n)

    new_base = args.new_base
    if not new_base:
        hosts = {r.url.split("/")[2] for r in new if r.host}
        if len(hosts) == 1:
            first = next(r for r in new if r.host)
            new_base = first.url.split("://")[0] + "://" + hosts.pop()
    cfg = MatchConfig(min_confidence=args.min_confidence, title_threshold=args.title_threshold,
                      fallback=args.fallback, new_base=new_base)
    result = build_map(old, new, overrides, cfg)
    entries = entries_from_matches(result.matches)
    flattened = _apply_flatten(result, entries) if args.flatten else 0
    if flattened:
        entries = entries_from_matches(result.matches)
    issues = validate(entries, new_urls=new, session=session, check=args.check_targets,
                      rate=args.check_rate)

    os.makedirs(args.out_dir, exist_ok=True)
    written = []
    skipped_cf = 0
    meta = {"tool": "redirect-mapper", "version": __version__,
            "similarity_backend": similarity.backend(), "flattened": flattened}
    for fmt in formats:
        path = os.path.join(args.out_dir, FILENAMES[fmt])
        if fmt == "nginx-map":
            text = to_nginx_map(result.matches)
        elif fmt == "nginx-return":
            text = to_nginx_return(result.matches)
        elif fmt == "apache":
            text = to_apache(result.matches, args.apache_style)
        elif fmt == "cloudflare":
            text, skipped_cf = to_cloudflare(result.matches)
        elif fmt == "wordpress":
            text = to_wordpress_redirection(result.matches)
        else:
            text = to_json(build_report(result, [i.to_dict() for i in issues], meta))
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        written.append(path)
    if result.unmatched:
        path = os.path.join(args.out_dir, "unmatched.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(unmatched_csv(result))
        written.append(path)

    if not args.quiet:
        _print_summary(console, result)
        _print_issues(console, issues)
        if skipped_cf:
            console.print("[yellow]Cloudflare: skipped %d row(s) without host or with query "
                          "string[/yellow]" % skipped_cf)
        for path in written:
            console.print("wrote " + path)
    if args.strict and has_errors(issues):
        return 1
    return 0


def cmd_validate(args: argparse.Namespace, console: Console,
                 session_factory: Callable[[], Any]) -> int:
    try:
        entries = load_redirect_csv(args.redirects)
        new = load_many(args.new, only_ok=True) if args.new else []
    except (LoadError, OSError) as exc:
        console.print("[red]error:[/red] %s" % exc)
        return 2
    session = session_factory() if args.check_targets else None
    issues = validate(entries, new_urls=new, session=session, check=args.check_targets,
                      rate=args.check_rate)
    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([i.to_dict() for i in issues], fh, indent=2, ensure_ascii=False)
    if not args.quiet:
        console.print("Checked %d redirect(s)" % len(entries))
        _print_issues(console, issues)
    return 1 if has_errors(issues) else 0


def cmd_verify(args: argparse.Namespace, console: Console,
               session_factory: Callable[[], Any]) -> int:
    try:
        entries = load_redirects(args.redirects)
    except (LoadError, OSError, VerifyInputError) as exc:
        console.print("[red]error:[/red] %s" % escape(str(exc)))
        return 2
    if not entries:
        console.print("[red]error:[/red] no redirects found in %s" % escape(args.redirects))
        return 2
    try:
        results = verify(entries, session_factory(), base=args.base, method=args.method,
                         rate=args.rate, max_hops=args.max_hops, timeout=args.timeout,
                         limit=args.limit)
    except VerifyInputError as exc:
        console.print("[red]error:[/red] %s" % escape(str(exc)))
        return 2
    summary = summarize(results)
    if args.json:
        meta = {"tool": "redirect-mapper", "version": __version__, "base": args.base,
                "method": args.method}
        with open(args.json, "w", encoding="utf-8", newline="") as fh:
            fh.write(to_report(results, meta))
    if not args.quiet:
        console.print("Verified %d source(s): %d ok, %d warning(s), %d error(s)"
                      % (summary["checked"], summary["ok"], summary["warnings"],
                         summary["errors"]))
        issues = [i for i in (r.to_issue() for r in results if r.status != OK) if i]
        if issues:
            _print_issues(console, issues, more="use --json for the full list")
    if summary["errors"] or (args.fail_on_warning and summary["warnings"]):
        return 1
    return 0


def cmd_inspect(args: argparse.Namespace, console: Console,
                session_factory: Callable[[], Any]) -> int:
    try:
        records = load_many(args.sources, fmt=args.format, session=session_factory(),
                            url_column=args.column)
    except LoadError as exc:
        console.print("[red]error:[/red] %s" % exc)
        return 2
    hosts = sorted({r.host or "(none)" for r in records})
    console.print("%d unique URL(s); hosts: %s" % (len(records), ", ".join(hosts) or "-"))
    for r in records[: args.limit]:
        console.print("  " + r.url)
    return 0


def main(argv: Optional[List[str]] = None,
         session_factory: Callable[[], Any] = make_session) -> int:
    args = build_parser().parse_args(argv)
    console = Console(highlight=False, soft_wrap=True)
    handlers = {"map": cmd_map, "validate": cmd_validate, "verify": cmd_verify,
                "inspect": cmd_inspect}
    return handlers[args.command](args, console, session_factory)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
