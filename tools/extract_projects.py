#!/usr/bin/env python3
"""Extract the archived projects into independent top-level directories.

The common top-level prefix of the archive (e.g. ``Projects-master/``) is
stripped and every file is placed directly under the destination root, so each
archived project becomes an independent top-level directory.

When a destination name already exists (an already extracted copy or a file
that was present before extraction), the archive entry keeps its name but
receives a suffix derived from the sha256 of its *source path* -- both copies
are preserved, byte for byte, so no file is ever lost.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_audit import (  # noqa: E402
    is_valid_relpath,
    sanitize_component,
    source_suffix,
    suffixed_leaf,
)


def strip_common_prefix(member: str, prefix: str) -> str:
    rel = member
    if rel.startswith(prefix):
        rel = rel[len(prefix):]
    return rel.lstrip("/")


def detect_common_prefix(names: list[str]) -> str:
    """Return the single shared top folder (with trailing slash), if any."""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    only_files_without_dirs = all("/" in n for n in names)
    if only_files_without_dirs and len(tops) == 1:
        return f"{next(iter(tops))}/"
    return ""


def safe_relpath(rel: str) -> str:
    """Ensure the relative name uses only characters the repo policy allows."""
    if is_valid_relpath(rel):
        return rel
    return "/".join(sanitize_component(part, rel) for part in rel.split("/"))


def unique_destination(rel: str, source: str, taken: set[str]) -> str:
    candidate = rel
    if candidate not in taken:
        taken.add(candidate)
        return candidate
    parts = candidate.split("/")
    parts[-1] = suffixed_leaf(parts[-1], source)
    candidate = "/".join(parts)
    if candidate in taken:  # astronomically unlikely, but stay correct
        counter = 2
        while "/".join(parts[:-1] + [f"{parts[-1]}-{counter}"]) in taken:
            counter += 1
        candidate = "/".join(parts[:-1] + [f"{parts[-1]}-{counter}"])
    taken.add(candidate)
    return candidate


def extract_zip(zip_path: Path, dest_root: Path):
    """Extract one archive; return (manifest, files_written, collisions).

    manifest: list of dicts with source/rel/dest/sha256/size for every file.
    """
    with zipfile.ZipFile(zip_path) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        prefix = detect_common_prefix([info.filename for info in infos])

        taken = set()
        for path in dest_root.rglob("*"):
            if path.is_file() and ".git" not in path.relative_to(dest_root).parts:
                taken.add(path.relative_to(dest_root).as_posix())

        manifest = []
        collisions = []
        for info in sorted(infos, key=lambda item: item.filename):
            source = info.filename
            rel = strip_common_prefix(source, prefix)
            if not rel or rel.startswith("..") or ":" in rel.split("/")[0]:
                raise ValueError(f"unsafe archive member: {source!r}")
            rel = safe_relpath(rel)
            collided = rel in taken
            dest_rel = unique_destination(rel, source, taken)
            if collided:
                collisions.append((source, dest_rel))

            data = zf.read(info)
            dest_path = dest_root / dest_rel
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(data)
            manifest.append({
                "source": source,
                "rel": rel,
                "dest": dest_rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })
    return manifest, len(manifest), collisions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", nargs="?", default="Projects-master.zip",
                        type=Path)
    parser.add_argument("--dest", default=Path("."), type=Path)
    args = parser.parse_args(argv)

    manifest, count, collisions = extract_zip(
        args.zip.resolve(), args.dest.resolve()
    )
    for source, dest_rel in collisions:
        print(f"name clash resolved (source-hash suffix): {source} -> {dest_rel}")
    print(f"extracted {count} file(s) from {args.zip.name}; "
          f"{len(collisions)} clash(es) resolved, no files dropped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
