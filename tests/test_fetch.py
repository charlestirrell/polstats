import hashlib
import json

import pytest

from polstats import fetch
from polstats.fetch import FetchError, FileSpec

BODY = b"year,state\n2024,TX\n"
MD5 = hashlib.md5(BODY).hexdigest()


def spec(**kw):
    d = dict(source="s", dest="f.csv", url="https://dataverse.harvard.edu/api/access/datafile/1", md5=MD5,
             mutable=False, requires_guestbook=False)
    return FileSpec(**{**d, **kw})


def test_real_manifest_is_wellformed():
    specs = fetch.load_manifest()
    assert {s.source for s in specs} == {"congress-legislators", "medsl-senate", "medsl-house"}
    for s in specs:
        assert s.mutable or (s.md5 and len(s.md5) == 32), f"{s.source}/{s.dest} must pin a checksum"
        assert s.url.startswith("https://")
    house = [s for s in specs if s.source == "medsl-house"]
    assert house and all(s.requires_guestbook for s in house)
    assert any("format=original" in s.url for s in specs)


def test_refuses_unlisted_hosts_and_unsafe_dest(tmp_path):
    with pytest.raises(FetchError, match="only https"):
        fetch.check_host("https://evil.example.com/x")
    with pytest.raises(FetchError, match="only https"):
        fetch.check_host("http://dataverse.harvard.edu/x")
    m = tmp_path / "m.toml"
    m.write_text('[[source]]\nname="x"\n[[source.file]]\nurl="https://unitedstates.github.io/a"\ndest="../up.json"\n')
    with pytest.raises(FetchError, match="unsafe dest"):
        fetch.load_manifest(m)


def test_guestbook_config_rejects_missing_and_placeholders(tmp_path):
    with pytest.raises(FetchError, match="guestbook"):
        fetch.load_guestbook(tmp_path / "nope.toml")
    p = tmp_path / "gb.toml"
    p.write_text('[guestbook]\nname="YOUR_NAME"\nemail="a@b.c"\ninstitution="x"\nposition="y"\n')
    with pytest.raises(FetchError, match="name"):
        fetch.load_guestbook(p)
    p.write_text('[guestbook]\nname="N"\nemail="a@b.c"\ninstitution="x"\nposition="y"\n')
    assert fetch.load_guestbook(p)["email"] == "a@b.c"


def test_example_config_is_all_placeholders(tmp_path):
    """The committed template must never carry real details."""
    ex = fetch.ROOT / "config" / "dataverse.example.toml"
    with pytest.raises(FetchError):
        fetch.load_guestbook(ex)


def test_download_verifies_skips_and_is_atomic(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "http", lambda m, u, h=None, b=None: (calls.append((m, u)) or (200, {}, BODY)))
    assert fetch.fetch_file(spec(), tmp_path).startswith("downloaded")
    assert (tmp_path / "s" / "f.csv").read_bytes() == BODY and not list(tmp_path.rglob("*.part"))
    assert fetch.fetch_file(spec(), tmp_path) == "present" and len(calls) == 1  # verified file is not re-fetched

    (tmp_path / "s" / "f.csv").write_bytes(b"corrupt")  # a corrupt copy is replaced
    assert fetch.fetch_file(spec(), tmp_path).startswith("downloaded") and len(calls) == 2


def test_checksum_mismatch_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "http", lambda *a, **k: (200, {}, b"tampered"))
    with pytest.raises(FetchError, match="checksum mismatch"):
        fetch.fetch_file(spec(), tmp_path)
    assert not (tmp_path / "s" / "f.csv").exists() and not list(tmp_path.rglob("*.part"))


def test_guestbook_flow_posts_details_and_follows_signed_url(tmp_path, monkeypatch):
    sent = []

    def fake(method, url, headers=None, body=None):
        sent.append((method, url, body))
        if method == "GET" and "datafile" in url:
            return 400, {}, b'{"status":"ERROR","message":"You may not download this file without the required Guestbook response"}'
        if method == "POST":
            return 200, {"content-type": "application/json"}, json.dumps({"data": {"signedUrl": "https://dataverse.harvard.edu/signed?x=1"}}).encode()
        return 200, {}, BODY

    monkeypatch.setattr(fetch, "http", fake)
    gb = {"name": "N", "email": "e@x.y", "institution": "I", "position": "P"}
    assert fetch.fetch_file(spec(requires_guestbook=True), tmp_path, guestbook=lambda: gb).startswith("downloaded")
    post = [s for s in sent if s[0] == "POST"][0]
    assert json.loads(post[2]) == {"guestbookResponse": gb}
    assert post[1].startswith("https://dataverse.harvard.edu/")  # details only ever go to Dataverse


def test_guestbook_not_requested_when_server_does_not_ask(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "http", lambda *a, **k: (200, {}, BODY))

    def boom():
        raise AssertionError("guestbook must not be read unless the server asks")
    assert fetch.fetch_file(spec(), tmp_path, guestbook=boom).startswith("downloaded")


def test_http_errors_become_fetch_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "http", lambda *a, **k: (404, {}, b'{"message":"Datafile not found"}'))
    with pytest.raises(FetchError, match="404.*not found"):
        fetch.fetch_file(spec(), tmp_path)
