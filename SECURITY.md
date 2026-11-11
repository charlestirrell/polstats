# Security policy

## Supported versions

polstats is early alpha (0.x). Only the latest commit on `main` is supported; there are no backported fixes.

## Reporting a vulnerability

Please report security problems **privately**; do not open a public issue.

1. Go to this repository's **Security** tab and choose **Report a vulnerability** (GitHub's private reporting).
2. If that option is unavailable, contact [@charlestirrell](https://github.com/charlestirrell) on GitHub and ask for a
   private channel, without including details in the public request.

Helpful details: the affected component and commit, steps to reproduce, and the impact you expect.

polstats is a volunteer-run project. The aim is to acknowledge a report within about a week and to credit reporters who
want credit. No fix timeline can be promised.

## What counts as a vulnerability

In scope, for example:

- SQL injection or other query manipulation through API search parameters or request bodies
- Cross-site scripting or script injection in the explorer UI (API data and the URL hash are untrusted input)
- Path traversal or arbitrary file access through the static file server or the fetch/loader commands
- Bypassing the host allow-list in `polstats.fetch`
- Your Dataverse guestbook details (`config/dataverse.local.toml`) being logged, committed, or sent anywhere other than
  `dataverse.harvard.edu`

## Known limitations that are by design

These are documented and are **not** vulnerabilities:

- **There is no authentication.** The API is read-only by default; `POST` and `PUT` return `403` unless the server is
  started with `POLSTATS_ALLOW_WRITES=1`. Enable writes only on a machine you control. Running a writable instance on a
  network is an unsupported configuration.
- **The development database password is public** (`polstats_dev`, in `docker-compose.yml`). The compose file binds
  Postgres to `127.0.0.1`. Exposing that port on a network is unsupported.
- The server listens on `127.0.0.1` by default. Binding it to other interfaces without adding authentication in front of it
  is unsupported.

Wrong or surprising *data* (for example a miscounted contest) is not a security issue; please open a normal issue with the
contest, person or source row involved.
