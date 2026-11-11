# Contributing to polstats

Thanks for helping. polstats is MIT-licensed; by contributing you agree your work is released under the same license.

## Ground rules

- **Non-partisan.** The project reports data and labels what is derived. Changes that add editorial framing won't
  be accepted; changes that make provenance, caveats and uncertainty clearer are very welcome.
- **No data in the repo.** Raw datasets and personal details never get committed. `tests/test_repo_hygiene.py`
  enforces this. Describe data in `sources.toml` instead; do not check it in.
- **Be kind.** Assume good faith, keep review comments about the code, and help newcomers.

## Development setup

```bash
docker compose up -d --wait       # PostgreSQL for tests and local runs
uv sync
export PYTHONPATH=src             # PowerShell: $env:PYTHONPATH = "src"
uv run pytest
```

Tests that need Postgres use a separate `polstats_test` database and skip if the server isn't reachable.

## Adding a data source

1. **Manifest:** add an entry to [`sources.toml`](sources.toml) with the license, and for fixed files a pinned URL or
   Dataverse file ID plus a checksum. Prefer the publisher's own checksum of the original file. Only `https` hosts on
   the allow-list in `polstats/fetch.py` can be fetched; add a host deliberately and explain why in the PR.
2. **Loader:** add `src/polstats/loaders/<name>.py`. Loaders must be:
   - **idempotent**: upsert by a natural-key identifier (`Store.upsert`) so a re-run creates no new versions;
   - **fail-loud**: raise on ambiguous or colliding keys instead of overwriting;
   - **provenance-writing**: record source, version (a content hash is fine) and license;
   - **honest about derived values**: flag anything you compute (see `ContestResult.outcomeDerived`).
3. **Tests:** cover the parsing quirks you found, idempotency, and at least one real-data-shaped fixture. Use small
   synthetic fixtures; do not commit real data.
4. **Docs:** update the data-sources table and known limitations in the README.

## Adding or changing a resource or search parameter

- Resources live in `src/polstats/resources.py` and must be added to `REGISTRY`; vocabularies go in `codesystems.py`.
- Search parameters are declared in `src/polstats/api/search.py`. Values are always bound as SQL parameters; never
  interpolate user input into SQL.
- The explorer UI (`src/polstats/web/`) reads only through the REST API. Chart colours follow the party, and every
  chart needs a table view and keyboard-accessible tooltips.

## Pull requests

- Keep them focused; describe what changed and why.
- Run `uv run pytest` before pushing and mention anything you could not test.
- Data-quality findings are contributions too: an issue describing a wrong or surprising value (with the contest,
  person or source row) is very useful.

## Reporting security issues

There is no authentication yet and the API is intended for local use. If you find a vulnerability, please open a
private security advisory on the repository rather than a public issue.
