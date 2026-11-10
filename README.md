# redirect-mapper

Build 301 redirect maps for site migrations. Feed it the old and new URL lists
(sitemaps, CSV, crawl exports) and it matches every old URL to the most likely new
one with a confidence score, then writes ready-to-deploy rules for Nginx, Apache,
Cloudflare and WordPress, plus a JSON report and an unmatched list for manual work.

- Inputs: XML sitemaps (index files, `.gz`, local or remote), CSV, text lists,
  Screaming Frog style crawl exports.
- Matching pipeline with scores, Bangla to Latin slug fallback, manual overrides.
- Validation: duplicate sources, chains, loops, redirects to 404/3xx targets
  (optional rate-limited HEAD checks).
- Runs on Python 3.9+. [rapidfuzz](https://pypi.org/project/rapidfuzz/) is optional;
  `difflib` is used when it is missing.

## Install

```bash
pip install .            # core
pip install ".[fast]"    # + rapidfuzz for large sites
```

## Quick start

```bash
redirect-mapper map \
  --old https://old.example.com/sitemap_index.xml \
  --new https://www.example.com/sitemap.xml \
  --overrides overrides.csv \
  --out-dir redirects \
  --check-targets
```

Try it on the bundled example:

```bash
redirect-mapper map --old examples/old-sitemap.xml --new examples/new-sitemap.xml \
  --overrides examples/overrides.csv --out-dir /tmp/redirects
```

`--out-dir` receives:

| File | Use |
| --- | --- |
| `nginx-redirect-map.conf` | `map $request_uri $redirect_target {...}` for `http {}` plus a one-line `if` per server |
| `nginx-redirects.conf` | `location = /old { return 301 ...; }` blocks, include in `server {}` |
| `redirects.htaccess` | Apache `RedirectMatch` (or `Redirect` with `--apache-style redirect`), `mod_rewrite` for `?query` sources |
| `cloudflare-bulk-redirects.csv` | Cloudflare Bulk Redirects list import |
| `wordpress-redirection.csv` | Import in the Redirection plugin (Tools > Import) |
| `report.json` | Summary, every match with method and confidence, unmatched, validation issues |
| `unmatched.csv` | Old URLs with no acceptable match plus the top 3 suggestions |

Limit output with `--formats nginx-map,json`.

## Inputs

`--old` and `--new` accept one or more sources, each a URL or a file:

| Source | Detection |
| --- | --- |
| Sitemap / sitemap index | `.xml`, `.xml.gz`, or XML content; children are followed (depth 3) |
| CSV | `.csv`; columns `Address`, `URL`, `Loc`, ...; `Title 1` and `Status Code` are used when present |
| Text | one URL or path per line, `#` comments |

Force a format with `--old-format` / `--new-format`, pick a CSV column with
`--old-column`. For the new list, rows with a non-2xx status are ignored. Bare paths
get a host from `--old-base` / `--new-base`.

## Matching pipeline

Each old URL is tried against these steps in order; the first hit wins.

| Step | Method | Confidence |
| --- | --- | --- |
| Manual overrides CSV (`old,new`) | `override` | 1.00 |
| Identical path (different host) | `exact-path` | 1.00 |
| Same path ignoring case / trailing slash | `exact-path-normalized` | 0.98 |
| Normalised path: lower-case, dates, IDs, extensions stripped, Bangla transliterated | `normalized-path` | 0.95 (0.80 if ambiguous) |
| Normalised last segment (slug) | `slug` | 0.90 (0.80 / 0.70 if ambiguous) |
| Page title similarity (needs titles; `--fetch-titles` or crawl export) | `title` | similarity x 0.9, max 0.90 |
| Fuzzy path + token overlap | `fuzzy-path` | score x 0.85, needs `--min-confidence` (default 0.6) |
| Parent path (`/a/b/c` to `/a/b`) | `parent-path` | 0.65, minus 0.05 per level |
| `--fallback URL` | `fallback` | 0.20 |

Old URLs whose path already exists unchanged on the same host are reported as
*unchanged* and get no rule. Anything left is written to `unmatched.csv`.

What normalisation does: `/2019/05/12/My_Post.html`, `/blog/123456-my-post` and
`/my-post-98765/` all become the slug `my-post`. Bangla slugs are transliterated
(`বাংলা` becomes `bangla`) before comparing; it is intentionally simple, so review
Bangla matches.

### Manual overrides

```csv
old,new
/old-promo-page,/offers/
https://old.example.com/shop/,https://www.example.com/store/
```

Overrides win over everything and may use paths or full URLs.

## Validation

Always run on `map`, and on its own for existing maps:

```bash
redirect-mapper validate redirects.csv --new https://www.example.com/sitemap.xml --check-targets
```

| Code | Severity | Meaning |
| --- | --- | --- |
| `loop`, `self-redirect` | error | Redirect never resolves |
| `duplicate-source` | error / warning | Same source twice (different / same target) |
| `chain` | warning | A to B to C; message shows the final target. `--flatten` fixes these |
| `target-not-in-new-list` | warning | Target not found in the new URL list |
| `source-is-live` | warning | Source also exists in the new list |
| `target-status`, `target-redirects`, `target-unreachable` | error | HEAD check found 4xx/5xx, a redirecting target, or a network error (`--check-targets`, `--check-rate` req/s) |

`--strict` makes `map` exit 1 when validation finds errors (useful in CI).
HEAD checks request each distinct target once, never follow redirects, and are
rate limited (default 5 requests per second).

## Deploying

**Nginx** (`nginx-redirect-map.conf`): put the `map` block in `http {}` and add
`if ($redirect_target) { return 301 $redirect_target; }` to each `server {}`.
`$request_uri` is the raw URI, so the map matches query strings too.

**Apache**: paste `redirects.htaccess` at the top of `.htaccess`. Apache matches the
decoded path, which the generator accounts for.

**Cloudflare**: Rules > Redirect Rules > Bulk Redirects > create a list > upload the CSV,
then add a Bulk Redirect rule using that list. Rows with query-string sources or
unknown hosts are skipped (reported). Check the column layout against Cloudflare's
current template before uploading.

**WordPress**: Redirection plugin > Tools > Import, choose CSV.

Always test with `curl -I` against a handful of URLs before and after deployment.

## All options

Run `redirect-mapper map --help`. Highlights: `--min-confidence`, `--title-threshold`,
`--fetch-titles`, `--fetch-rate`, `--fallback`, `--flatten`, `--check-targets`,
`--strict`, `--no-rapidfuzz`, `-q`.

## Development

```bash
pip install -e ".[dev,fast]"
pytest -q
ruff check .
```

## License

MIT. Author: [Saizul Amin](https://saizul.com).
