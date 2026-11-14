# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `redirect-mapper verify`: post-deployment check that requests every source
  (from a `source,target` CSV or the `report.json` written by `map`) without
  following redirects, walks the live `Location` chain (bounded by `--max-hops`)
  and reports missing rules, wrong targets, 4xx/5xx destinations, live loops,
  302/307 instead of 301/308, multi-hop chains and slash/case/scheme variants.
  Rate limited, `HEAD` with `GET` fallback, `--base` to test a staging host,
  `--limit`, `--json` and `--fail-on-warning` for CI gates.

### Fixed
- URLs and messages in the validation table are escaped, so `[...]` in a URL or
  `Location` header is no longer interpreted as console markup.

## [0.1.0] - 2026-11-10

### Added
- `redirect-mapper map`: matching pipeline (manual overrides, exact path,
  normalised path, slug, title similarity, fuzzy path, parent path) with
  confidence scores and an unmatched report with suggestions.
- Inputs: sitemaps (index, gzip, local or remote), CSV, plain text and
  Screaming Frog style crawl exports.
- Simple Bangla to Latin transliteration for slug comparison.
- Outputs: Nginx `map`, Nginx `return 301` include, Apache `.htaccess`
  (`RedirectMatch` or `Redirect`, `mod_rewrite` for query sources), Cloudflare
  Bulk Redirects CSV, WordPress Redirection plugin CSV and a JSON report.
- Validation: duplicate sources, chains, loops, self redirects, live sources,
  unknown targets and optional rate-limited HEAD checks (`--check-targets`),
  plus `--flatten` to collapse chains.
- `redirect-mapper validate` for existing redirect CSVs and `inspect`.
- rapidfuzz is optional; difflib is used otherwise.
