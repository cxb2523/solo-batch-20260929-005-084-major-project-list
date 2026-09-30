"""Tests for project extraction and the repository hygiene audit.

The two behaviours the task explicitly calls out are named so they read
straight out of the pytest report:

  * test_duplicate_name_conflict_keeps_both_files_with_source_hash_suffix
  * test_allowlist_rename_bypass_is_rejected
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import extract_projects  # noqa: E402
import repo_audit  # noqa: E402


def make_zip(path: Path, entries: dict[str, bytes], prefix: str = "Proj/"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(prefix.rstrip("/") + "/", b"")
        for name, data in entries.items():
            zf.writestr(prefix + name, data)


def test_duplicate_name_conflict_keeps_both_files_with_source_hash_suffix(tmp_path):
    """Same leaf name from two source paths must survive, hash-disambiguated."""
    first = tmp_path / "a.zip"
    make_zip(
        first,
        {"alpha/README.md": b"# Alpha\ncontents A\n",
         "data.txt": b"shared leaf from alpha\n"},
        prefix="Projects-master/",
    )

    manifest_a, count_a, _ = extract_projects.extract_zip(first, tmp_path / "out")
    assert count_a == 2
    assert (tmp_path / "out" / "alpha" / "README.md").read_bytes() == (
        b"# Alpha\ncontents A\n"
    )
    original = tmp_path / "out" / "data.txt"
    assert original.exists()

    second = tmp_path / "b.zip"
    make_zip(
        second,
        {"beta/README.md": b"# Beta\ncontents B\n",
         "data.txt": b"shared leaf from beta -- must not be lost\n"},
        prefix="Projects-master/",
    )

    manifest_b, count_b, collisions_b = extract_projects.extract_zip(
        second, tmp_path / "out"
    )
    assert count_b == 2

    # A clash was detected and resolved with a source-path hash suffix.
    collision_dests = {dest for _, dest in collisions_b}
    conflict = tmp_path / "out" / (
        "data-" + repo_audit.source_suffix("Projects-master/data.txt") + ".txt"
    )
    assert conflict in {tmp_path / "out" / dest for dest in collision_dests}

    # Both copies exist with their original, distinct bytes -> nothing lost.
    assert original.read_bytes() == b"shared leaf from alpha\n"
    assert conflict.read_bytes() == (
        b"shared leaf from beta -- must not be lost\n"
    )

    # The distinct README files both survive untouched (no clash there).
    assert (tmp_path / "out" / "alpha" / "README.md").read_bytes() == (
        b"# Alpha\ncontents A\n"
    )
    assert (tmp_path / "out" / "beta" / "README.md").read_bytes() == (
        b"# Beta\ncontents B\n"
    )

    # Every extracted file still satisfies the filename charset.
    for item in manifest_b:
        assert repo_audit.is_valid_relpath(item["dest"])
    print("\nASSERT PASSED: duplicate-name conflict keeps BOTH files, "
          "disambiguated by a source-path hash suffix")


def test_allowlist_rename_bypass_is_rejected(tmp_path):
    """A hash allowlisted for one path must not authorise a renamed file."""
    legit = tmp_path / "big.bin"
    payload = b"x" * (repo_audit.LIMIT + 1)
    legit.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    allowlist = tmp_path / "allow.txt"
    allowlist.write_text(f"big.bin {digest}\n", encoding="utf-8")
    loaded = repo_audit.load_allowlist(allowlist)

    # The exact path + hash is allowed ...
    assert repo_audit.large_file_status("big.bin", len(payload), digest,
                                        loaded) is None

    # ... but the same bytes under a new, unlisted path fail (rename bypass).
    renamed_reason = repo_audit.large_file_status(
        "renamed-big.bin", len(payload), digest, loaded
    )
    assert renamed_reason is not None
    assert "allowlist" in renamed_reason

    # A file at the allowlisted path but with different content also fails.
    tampered = b"y" * (repo_audit.LIMIT + 1)
    tampered_digest = hashlib.sha256(tampered).hexdigest()
    tamper_reason = repo_audit.large_file_status(
        "big.bin", len(tampered), tampered_digest, loaded
    )
    assert tamper_reason is not None
    assert "sha256" in tamper_reason

    # Small files never need an allowlist entry.
    assert repo_audit.large_file_status("anything.small", 10,
                                        "0" * 64, loaded) is None
    print("\nASSERT PASSED: allowlist cannot be bypassed by renaming a large "
          "file (path AND sha256 must both match)")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_fix_mode_renames_invalid_path_with_git_mv_then_reports(tmp_path):
    """fix mode must use git mv and still exit non-zero on the violation."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    bad_rel = "weird $name!.txt"
    bad_path = tmp_path / bad_rel
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "--all"], cwd=tmp_path, check=True)

    assert not repo_audit.is_valid_relpath(bad_rel)

    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("# empty\n", encoding="utf-8")
    code = repo_audit.run(tmp_path, check=False, allowlist_path=allowlist)

    assert code == 1  # violation reported loudly even after the rename
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path, check=True,
        stdout=subprocess.PIPE, text=True,
    ).stdout.splitlines()
    assert "weird-name.txt" in tracked
    assert bad_rel not in tracked
    assert (tmp_path / "weird-name.txt").read_text(encoding="utf-8") == "hello"


def test_check_mode_passes_on_clean_tree(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "good_name.txt").write_text("ok", encoding="utf-8")
    subprocess.run(["git", "add", "--all"], cwd=tmp_path, check=True)
    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("", encoding="utf-8")
    code = repo_audit.run(tmp_path, check=True, allowlist_path=allowlist)
    assert code == 0
