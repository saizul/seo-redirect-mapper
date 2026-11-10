# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
