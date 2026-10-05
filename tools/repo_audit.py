"""Repository hygiene audit.

Checks every file tracked by git:

* each path component may only contain ``A-Za-z0-9._-`` (``/`` is the
  path separator).  Violating files are first renamed with ``git mv``
  and then reported, so the workspace is left clean.
* files larger than 1 MiB are rejected unless their path *and* sha256
  appear together in ``.github/binary-allowlist.txt``.  Renaming a file
  does not bypass the check, because the allowlisted path must match too.

Usage: python tools/repo_audit.py --check [--root PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

MAX_FILE_SIZE = 1024 * 1024  # 1 MiB
COMPONENT_RE = re.compile(r"[A-Za-z0-9._-]+")
INVALID_CHARS = re.compile(r"[^A-Za-z0-9._-]")
ALLOWLIST_PATH = PurePosixPath(".github/binary-allowlist.txt")


def sanitize_component(name: str) -> str:
    cleaned = INVALID_CHARS.sub("-", name).strip("-.")
    return cleaned or "unnamed"


def sanitize_path(path: str) -> str:
    return "/".join(sanitize_component(part) for part in path.split("/"))


def is_valid_path(path: str) -> bool:
    return all(COMPONENT_RE.fullmatch(part) for part in path.split("/"))


def source_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    return [
        entry
        for entry in result.stdout.decode("utf-8", "surrogateescape").split("\0")
        if entry
    ]


def unique_target(root: Path, path: str) -> str:
    if not (root / path).exists():
        return path
    posix = PurePosixPath(path)
    suffix = source_hash(path)
    candidate = str(posix.with_name(f"{posix.stem}-{suffix}{posix.suffix}"))
    counter = 2
    while (root / candidate).exists():
        candidate = str(
            posix.with_name(f"{posix.stem}-{suffix}-{counter}{posix.suffix}")
        )
        counter += 1
    return candidate


def git_mv(root: Path, src: str, dst: str) -> None:
    dst_abs = root / dst
    dst_abs.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "mv", src, dst], cwd=root, capture_output=True
    )
    if result.returncode != 0:
        # Fall back to a plain rename when the file is not in the index yet.
        os.replace(root / src, dst_abs)
        subprocess.run(
            ["git", "add", "--", dst], cwd=root, capture_output=True
        )


def load_allowlist(root: Path) -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    allowlist_file = root / ALLOWLIST_PATH
    if not allowlist_file.exists():
        return entries
    for line in allowlist_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, rel_path = parts
        entries.add((digest.lower(), rel_path))
    return entries


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_check(root) -> int:
    root = Path(root).resolve()
    problems = 0

    for path in tracked_files(root):
        if is_valid_path(path):
            continue
        target = unique_target(root, sanitize_path(path))
        git_mv(root, path, target)
        print(f"renamed: {path} -> {target}")
        problems += 1

    allowlist = load_allowlist(root)
    for path in tracked_files(root):
        file_path = root / path
        try:
            size = file_path.stat().st_size
        except OSError:
            continue
        if size <= MAX_FILE_SIZE:
            continue
        digest = sha256_file(file_path)
        if (digest, path) in allowlist:
            print(f"allowed: {path} ({size} bytes, sha256 allowlisted)")
            continue
        print(
            f"oversize: {path} ({size} bytes > {MAX_FILE_SIZE}); "
            f"no matching path+sha256 entry in {ALLOWLIST_PATH}"
        )
        problems += 1

    if problems:
        print(f"repo-audit: {problems} problem(s) found")
        return 1
    print("repo-audit: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="run the hygiene checks"
    )
    parser.add_argument(
        "--root", default=None, help="repository root (default: auto-detect)"
    )
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 2
    if args.root:
        root = Path(args.root)
    else:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True
        )
        root = (
            Path(result.stdout.decode().strip())
            if result.returncode == 0
            else Path(__file__).resolve().parents[1]
        )
    return run_check(root)


if __name__ == "__main__":
    raise SystemExit(main())
