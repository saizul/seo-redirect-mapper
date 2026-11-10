# Contributing

Bug reports and pull requests are welcome.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,fast]"
pytest -q
ruff check .
```

The code must run on Python 3.9+ (CI covers 3.10 - 3.13). Use
`from __future__ import annotations` and avoid syntax newer than 3.9 at runtime
(`match`, `X | Y` outside annotations).

## Guidelines

- Add tests for every change; tests must not touch the network. Use `FakeSession`
  from `tests/conftest.py`.
- Run the suite both with and without rapidfuzz installed; both backends must give
  the same results for the same inputs.
- Output writers must never emit unescaped user data into server configs. Add a
  test with hostile input (quotes, `$`, `;`, `{`) when you touch them.
- Keep examples on `example.com` / `example.org`.
- Update `README.md` and `CHANGELOG.md` for user-facing changes.

## Adding a matching strategy

Strategies live in `redirect_mapper/matching.py::match_one`, in priority order.
Return a `Match` with a method name and a confidence between 0 and 1, document the
score in the README table, and add tests that show it does not outrank a more
reliable strategy.
