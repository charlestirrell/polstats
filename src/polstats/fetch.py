"""Fetch the raw data files listed in sources.toml into raw/<source>/ (git-ignored).

    PYTHONPATH=src uv run python -P -m polstats.fetch              # everything
    PYTHONPATH=src uv run python -P -m polstats.fetch medsl-senate # one source
    PYTHONPATH=src uv run python -P -m polstats.fetch --list
    PYTHONPATH=src uv run python -P -m polstats.fetch --refresh    # re-download mutable sources

Reproducible: files are pinned by Dataverse file id and verified against the MD5 Dataverse publishes for the
original upload. Files that already verify are skipped. Standard library only.

Guestbook: Dataverse serves some files (the MEDSL House file) only after a "guestbook response". When the server
asks for one, the details are read from config/dataverse.local.toml (git-ignored) and sent to
dataverse.harvard.edu, and nowhere else. They are never printed or logged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "sources.toml"
RAW_DIR = ROOT / "raw"
GUESTBOOK_CONFIG = ROOT / "config" / "dataverse.local.toml"
DATAVERSE = "https://dataverse.harvard.edu"
ALLOWED_HOSTS = {"dataverse.harvard.edu", "unitedstates.github.io"}
USER_AGENT = "polstats-fetch/0.1"  # generic on purpose: no personal details in headers


class FetchError(Exception):
    pass


@dataclass
class FileSpec:
    source: str
    dest: str
    url: str
    md5: str | None
    mutable: bool
    requires_guestbook: bool

    def path(self, raw_dir: Path) -> Path:
        return raw_dir / self.source / self.dest


# ---------- manifest / config ----------
def load_manifest(path: Path = MANIFEST) -> list[FileSpec]:
    data = tomllib.loads(path.read_text())
    specs: list[FileSpec] = []
    for src in data.get("source", []):
        for f in src.get("file", []):
            if "dataverse_id" in f:
                url = f"{DATAVERSE}/api/access/datafile/{int(f['dataverse_id'])}"
                if f.get("original"):
                    url += "?format=original"
            elif "url" in f:
                url = f["url"]
            else:
                raise FetchError(f"{src['name']}: file entry needs dataverse_id or url")
            check_host(url)
            if "/" in f["dest"] or f["dest"].startswith("."):
                raise FetchError(f"{src['name']}: unsafe dest {f['dest']!r}")
            specs.append(FileSpec(src["name"], f["dest"], url, f.get("md5"), bool(src.get("mutable")),
                                  bool(src.get("requires_guestbook"))))
    return specs


def check_host(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS:
        raise FetchError(f"refusing to fetch {url!r}: only https from {sorted(ALLOWED_HOSTS)} is allowed")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_guestbook(path: Path = GUESTBOOK_CONFIG) -> dict[str, str]:
    if not path.exists():
        raise FetchError(f"this file needs a Dataverse guestbook response. Copy config/dataverse.example.toml to "
                         f"{_rel(path)} and fill it in (it is git-ignored).")
    gb = tomllib.loads(path.read_text()).get("guestbook", {})
    fields = ("name", "email", "institution", "position")
    missing = [k for k in fields if not str(gb.get(k, "")).strip() or str(gb[k]).startswith("YOUR_")]
    if missing:
        raise FetchError(f"{_rel(path)} still has placeholder/empty values for: {', '.join(missing)}")
    return {k: str(gb[k]).strip() for k in fields}


# ---------- network ----------
def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except urllib.error.URLError as e:
        raise FetchError(f"network error for {urllib.parse.urlparse(url).hostname}: {e.reason}")


def _message(data: bytes) -> str:
    try:
        return str(json.loads(data).get("message", ""))[:300]
    except (ValueError, AttributeError):
        return data[:200].decode("utf-8", "replace")


def download(spec: FileSpec, guestbook=load_guestbook) -> bytes:
    status, _, data = http("GET", spec.url)
    if status == 400 and b"uestbook" in data:
        body = json.dumps({"guestbookResponse": guestbook()}).encode()
        status, hdr, data = http("POST", spec.url, {"Content-Type": "application/json"}, body)
        if status == 200 and "json" in hdr.get("content-type", ""):
            try:  # newer Dataverse versions answer with a signed URL to fetch next
                signed = json.loads(data)["data"]["signedUrl"]
            except (ValueError, KeyError, TypeError):
                signed = None
            if signed:
                if urllib.parse.urlparse(signed).scheme != "https":
                    raise FetchError("server returned a non-https download URL; refusing")
                status, hdr, data = http("GET", signed)
    if status != 200:
        raise FetchError(f"HTTP {status}: {_message(data)}")
    return data


def md5_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324 - integrity check against the repository's published MD5


# ---------- orchestration ----------
def fetch_file(spec: FileSpec, raw_dir: Path, refresh: bool = False, guestbook=load_guestbook) -> str:
    dest = spec.path(raw_dir)
    if dest.exists() and not (spec.mutable and refresh):
        if spec.mutable or md5_of(dest.read_bytes()) == spec.md5:
            return "present"
    data = download(spec, guestbook)
    if spec.md5 and md5_of(data) != spec.md5:
        raise FetchError(f"checksum mismatch for {spec.dest}: got {md5_of(data)}, manifest has {spec.md5}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return f"downloaded {len(data):,} bytes" + (", checksum ok" if spec.md5 else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sources", nargs="*", help="source names (default: all)")
    ap.add_argument("--list", action="store_true", help="list sources and exit")
    ap.add_argument("--refresh", action="store_true", help="re-download mutable sources")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    args = ap.parse_args(argv)

    specs = load_manifest(args.manifest)
    names = sorted({s.source for s in specs})
    if args.list:
        for n in names:
            files = [s for s in specs if s.source == n]
            flags = ("mutable " if files[0].mutable else "") + ("guestbook" if files[0].requires_guestbook else "")
            print(f"{n:22} {len(files)} file(s) {flags}".rstrip())
        return 0
    unknown = set(args.sources) - set(names)
    if unknown:
        print(f"unknown source(s): {', '.join(sorted(unknown))}. Known: {', '.join(names)}", file=sys.stderr)
        return 2

    failures = 0
    for spec in specs:
        if args.sources and spec.source not in args.sources:
            continue
        try:
            status = fetch_file(spec, args.raw_dir, args.refresh)
            print(f"ok    {spec.source}/{spec.dest}: {status}")
        except FetchError as e:
            failures += 1
            print(f"FAIL  {spec.source}/{spec.dest}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
