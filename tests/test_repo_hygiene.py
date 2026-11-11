"""Guard rails for a public repo: raw data and personal config must never become tracked.

These inspect git's view of the working tree, so they are skipped outside a git repository.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or git("rev-parse", "--is-inside-work-tree").returncode != 0,
    reason="not a git repository")

DATA_SUFFIXES = {".tab", ".tsv", ".csv", ".json", ".parquet", ".xlsx", ".zip", ".gz", ".dump"}
ALLOWED_DATA = {"package.json"}  # none today; list here deliberately if ever needed


def untracked_and_tracked() -> list[str]:
    """Everything git would include in a commit: tracked files plus untracked-but-not-ignored."""
    out = git("ls-files", "--cached", "--others", "--exclude-standard").stdout.split("\n")
    return [p for p in out if p]


def test_no_raw_data_or_local_config_would_be_committed():
    files = untracked_and_tracked()
    assert files, "expected some files"
    offenders = [p for p in files
                 if p.startswith("raw/") or ".local." in Path(p).name
                 or (Path(p).suffix in DATA_SUFFIXES and Path(p).name not in ALLOWED_DATA)]
    assert not offenders, f"data/personal files would be committed: {offenders}"


def test_no_large_files_would_be_committed():
    big = [p for p in untracked_and_tracked() if (ROOT / p).is_file() and (ROOT / p).stat().st_size > 1_000_000]
    assert not big, f"files over 1 MB would be committed (data?): {big}"


@pytest.mark.parametrize("path", ["raw/medsl-house/1976-2024-house.tsv", "raw/anything.csv",
                                  "config/dataverse.local.toml", "x/y/foo.tab"])
def test_sensitive_paths_are_ignored(path):
    assert git("check-ignore", "-q", path).returncode == 0, f"{path} is not git-ignored"


def test_example_config_is_tracked_not_ignored():
    assert git("check-ignore", "-q", "config/dataverse.example.toml").returncode == 1
