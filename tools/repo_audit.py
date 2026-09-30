#!/usr/bin/env python3
"""Repository hygiene audit.

Rules (applied to every file reported by ``git ls-files``):

1. Path names may only contain ``A-Za-z0-9._-`` in each ``/``-separated part.
2. Files larger than ``LIMIT`` (1 MiB) fail unless BOTH their repository
   relative path AND their sha256 digest match an entry in
   ``.github/binary-allowlist.txt`` -- renaming a file can therefore never
   smuggle it through.

Modes:
  --check : report only; exit non-zero when anything violates a rule. This is
            what CI runs, and it never touches the working tree.
  fix     : offending paths are renamed with ``git mv`` (illegal characters are
            sanitised, collisions disambiguated with a hash of the original
            source path) so nothing is dropped or left as a stray file; the run
            still exits non-zero to make the violation loudly visible.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

LIMIT = 1024 * 1024
HASH_LEN = 8

_PART_RE = re.compile(r"[A-Za-z0-9._-]+\Z")
_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_REPEATED_DASH_RE = re.compile(r"-{2,}")


def source_suffix(source: str) -> str:
    """Short, stable disambiguation suffix derived from a source path."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return digest[:HASH_LEN]


def is_valid_relpath(rel: str) -> bool:
    """True when every path part uses only the approved character set."""
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return all(_PART_RE.fullmatch(part) is not None for part in parts)


def sanitize_component(name: str, source: str = "") -> str:
    """Map a single path component onto ``[A-Za-z0-9._-]``."""
    def clean_segment(seg: str) -> str:
        seg = "".join(ch if ch.isascii() and ch.isalnum() or ch in "._-" else "-"
                      for ch in seg)
        seg = _REPEATED_DASH_RE.sub("-", seg).strip("-")
        return seg

    stem, dot, suffix = name.rpartition(".")
    if dot and stem:
        clean_stem = clean_segment(stem)
        clean_suffix = clean_segment(suffix)
        if clean_suffix:
            if clean_stem:
                return f"{clean_stem}.{clean_suffix}"
            return f"file-{source_suffix(source or name)}.{clean_suffix}"

    cleaned = clean_segment(name)
    if not cleaned or set(cleaned) <= {".", "_", "-"}:
        cleaned = "file-" + source_suffix(source or name)
    return cleaned


def suffixed_leaf(leaf: str, source: str) -> str:
    """Insert a source-path hash before the extension of *leaf*."""
    stem, dot, ext = leaf.rpartition(".")
    if not stem or "/" in stem or stem in (".", ".."):
        return f"{leaf}-{source_suffix(source)}"
    return f"{stem}-{source_suffix(source)}{dot}{ext}"


def sanitize_relpath(rel: str) -> str:
    """Sanitise every component of a repository relative path."""
    return "/".join(sanitize_component(part, rel) for part in rel.split("/"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_allowlist(text: str) -> dict:
    """Parse ``<path> <sha256>`` lines, one entry per line."""
    allowlist: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) != 2:
            raise ValueError(
                f"allowlist line {lineno} must contain one path and one 64-char "
                f"sha256: {raw!r}"
            )
        rel, digest = tokens
        if not _HEX64_RE.fullmatch(digest.lower()):
            raise ValueError(
                f"allowlist line {lineno} has an invalid sha256: {raw!r}"
            )
        rel = rel.replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        allowlist[rel] = digest.lower()
    return allowlist


def load_allowlist(path: Path) -> dict:
    if not path.is_file():
        return {}
    return parse_allowlist(path.read_text(encoding="utf-8"))


def large_file_status(rel: str, size: int, digest: str, allowlist: dict):
    """Return None when acceptable, otherwise a human readable reason."""
    if size <= LIMIT:
        return None
    expected = allowlist.get(rel)
    if expected is not None and expected == digest.lower():
        return None
    if expected is None:
        return (f"is {size} bytes (> {LIMIT}) and is not listed in the binary "
                f"allowlist (a rename cannot grant an exception)")
    return (f"is {size} bytes (> {LIMIT}) and its sha256 does not match the "
            f"allowlist entry for that path")


def list_tracked(root: Path):
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True, stdout=subprocess.PIPE,
    )
    raw_items = proc.stdout.split(b"\x00")
    return [item.decode("utf-8", "surrogateescape")
            for item in raw_items if item]


def git_mv(root: Path, src: str, dst: str) -> None:
    dst_path = root / dst
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "mv", "--", src, dst],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run(root: Path, check: bool, allowlist_path: Path) -> int:
    tracked = list_tracked(root)

    invalid = [rel for rel in tracked if not is_valid_relpath(rel)]
    renamed = []
    if invalid and not check:
        taken = set(tracked)
        for rel in invalid:
            parts = [sanitize_component(part, rel)
                     for part in rel.split("/")]
            new_rel = "/".join(parts)
            if new_rel in taken and new_rel != rel:
                parts[-1] = suffixed_leaf(parts[-1], rel)
                new_rel = "/".join(parts)
            git_mv(root, rel, new_rel)
            taken.discard(rel)
            taken.add(new_rel)
            renamed.append((rel, new_rel))
        tracked = list_tracked(root)
        still_invalid = [rel for rel in tracked if not is_valid_relpath(rel)]
    else:
        still_invalid = list(invalid)

    failures = []
    for rel in sorted(still_invalid):
        failures.append(
            f"invalid filename: {rel!r} (only A-Za-z0-9._- and '/' are allowed)"
        )
    for old, new in renamed:
        print(f"renamed via git mv: {old} -> {new}")

    allowlist = load_allowlist(allowlist_path)
    if not allowlist_path.is_file():
        print(f"note: no allowlist at {allowlist_path}, every file over "
              f"{LIMIT} bytes fails")
    for rel in sorted(tracked):
        path = root / rel
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size <= LIMIT:
            continue
        reason = large_file_status(rel, size, sha256_file(path), allowlist)
        if reason is not None:
            failures.append(f"{rel}: {reason}")

    if renamed:
        print(f"{len(renamed)} path(s) were renamed to satisfy the filename "
              f"charset; please stage and re-run the audit.")
    if failures:
        print("repo hygiene violations:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    mode = "check" if check else "fix"
    if renamed:
        print(f"repo hygiene {mode} renamed {len(renamed)} offending path(s); "
              f"the run still fails so the violation is visible. Re-run "
              f"--check after staging.", file=sys.stderr)
        return 1

    print(f"repo hygiene {mode} passed: {len(tracked)} tracked file(s), "
          f"all names valid, no unauthorised large files.")
    return 0


def main(argv=None) -> int:
    script_root = Path(__file__).resolve().parent
    repo_root = script_root.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report only; never modify the working tree")
    parser.add_argument("--root", type=Path, default=repo_root)
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=repo_root / ".github" / "binary-allowlist.txt",
    )
    args = parser.parse_args(argv)
    return run(args.root.resolve(), args.check, args.allowlist.resolve())


if __name__ == "__main__":
    sys.exit(main())
