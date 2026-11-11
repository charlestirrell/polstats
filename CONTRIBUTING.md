# Contributing to polstats

Thanks for helping. polstats is MIT-licensed; by contributing you agree your work is released under the same license.

## Ground rules

- **Non-partisan.** The project reports data and labels what is derived. Changes that add editorial framing won't
  be accepted; changes that make provenance, caveats and uncertainty clearer are very welcome.
- **No data in the repo.** Raw datasets and personal details never get committed. `tests/test_repo_hygiene.py`
  enforces this. Describe data in `sources.toml` instead; do not check it in.
- **Be kind.** Follow the [Code of Conduct](CODE_OF_CONDUCT.md). Assume good faith, keep review comments about the code,
  and help newcomers.

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
- Views must respect the global filters: members go through `memberMatches` and contests through `contestMatches`
  (`filters.js`), and charts should handle "nothing matches" with an empty state. Filter state lives in the URL hash
  (`filtersToHash` / `filtersFromHash`), which is untrusted input: validate everything you read from it.
- The UI has no JavaScript test runner, but it has an in-page smoke test: [tests/ui/smoke.js](tests/ui/smoke.js) drives
  the real controls and cross-checks counts with the API (see [tests/ui/README.md](tests/ui/README.md)). Run it after any UI
  change, add a check for what you add, and also try a zero-result query and a phone-width window by hand.
- Keep the two views separate. Anything that belongs to *current members* goes in "Congress now" and anything about *past
  elections* in "Election history"; say in the card text which one a number comes from, and never mix the two datasets in one
  count. Wording matters to analysts: prefer plain terms ("Election type", "Vote share") over internal ones.

## Pull requests

- Keep them focused; describe what changed and why.
- Run `uv run pytest` before pushing and mention anything you could not test.
- Data-quality findings are contributions too: an issue describing a wrong or surprising value (with the contest,
  person or source row) is very useful.

## Reporting security issues

See [SECURITY.md](SECURITY.md). There is no authentication yet. The API is read-only by default (writes need
`POLSTATS_ALLOW_WRITES=1`) and is intended for local use. If you find a vulnerability, please open a
private security advisory on the repository rather than a public issue.
