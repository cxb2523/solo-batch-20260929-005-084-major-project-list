"""Tests for tools/extract_projects.py, tools/repo_audit.py and
tools/build_index.py."""
from __future__ import annotations

import hashlib
import subprocess
import zipfile

from tools import build_index, extract_projects, repo_audit


def _make_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _init_repo(root):
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")


def test_extract_name_collision_keeps_everything(tmp_path):
    zip_a = tmp_path / "a.zip"
    zip_b = tmp_path / "b.zip"
    _make_zip(zip_a, {
        "Demo-master/README.md": "readme A",
        "Demo-master/src/app.py": "print('a')",
    })
    _make_zip(zip_b, {"Demo-main/README.md": "readme B"})
    dest = tmp_path / "out"
    dest.mkdir()

    map_a = extract_projects.extract_zip(zip_a, dest)
    map_b = extract_projects.extract_zip(zip_b, dest)

    first = dest / "Demo"
    suffix = hashlib.sha1("Demo-main".encode("utf-8")).hexdigest()[:8]
    second = dest / f"Demo-{suffix}"
    assert map_a["Demo-master"] == first
    assert map_b["Demo-main"] == second
    # both projects coexist and no file was lost
    assert (first / "README.md").read_text(encoding="utf-8") == "readme A"
    assert (first / "src" / "app.py").read_text(encoding="utf-8") == "print('a')"
    assert (second / "README.md").read_text(encoding="utf-8") == "readme B"


def test_allowlist_rename_cannot_bypass_size_check(tmp_path):
    _init_repo(tmp_path)
    payload = b"\xab" * (repo_audit.MAX_FILE_SIZE + 1)
    digest = hashlib.sha256(payload).hexdigest()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "renamed.bin").write_bytes(payload)
    allowlist_dir = tmp_path / ".github"
    allowlist_dir.mkdir()
    allowlist = allowlist_dir / "binary-allowlist.txt"
    # allowlist entry names a *different* path with the correct sha256
    allowlist.write_text(f"{digest}  data/blob.bin\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")

    # renaming the file must not bypass the size check
    assert repo_audit.run_check(tmp_path) == 1

    # path and sha256 both matching the allowlist passes
    _git(tmp_path, "mv", "data/renamed.bin", "data/blob.bin")
    assert repo_audit.run_check(tmp_path) == 0

    # a wrong sha256 in the allowlist fails again
    allowlist.write_text(f"{'0' * 64}  data/blob.bin\n", encoding="utf-8")
    assert repo_audit.run_check(tmp_path) == 1


def test_invalid_filename_is_git_moved_then_reported(tmp_path):
    _init_repo(tmp_path)
    bad = tmp_path / "bad name.txt"
    bad.write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "-A")

    assert repo_audit.run_check(tmp_path) == 1
    assert not bad.exists()
    assert (tmp_path / "bad-name.txt").exists()
    # workspace is clean now: a second run passes
    assert repo_audit.run_check(tmp_path) == 0


def test_build_index_is_idempotent(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    project = tmp_path / "ProjA"
    project.mkdir()
    (project / "README.md").write_text(
        "# ProjA\n\nA python calculator for numbers.\n", encoding="utf-8"
    )
    (project / "main.py").write_text("print('hi')\n", encoding="utf-8")

    assert build_index.main(tmp_path) == 0
    first_readme = (tmp_path / "README.md").read_bytes()
    first_html = (tmp_path / "index.html").read_bytes()
    assert b"PROJECT-INDEX:BEGIN" in first_readme
    assert b"ProjA" in first_html

    assert build_index.main(tmp_path) == 0
    assert (tmp_path / "README.md").read_bytes() == first_readme
    assert (tmp_path / "index.html").read_bytes() == first_html
