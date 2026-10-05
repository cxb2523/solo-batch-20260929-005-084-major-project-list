"""Extract project archives into independent top-level directories.

Each top-level entry inside the archive is treated as one project.  The
archive's top-level prefix (for example ``Projects-master/``) is stripped:
GitHub-style ``-master``/``-main`` suffixes are removed and the remaining
name becomes the project directory.  When two projects map to the same
directory name, later ones get a suffix derived from a hash of their
source path so that both coexist and no file is ever lost.

Path components are sanitised to ``A-Za-z0-9._-`` so the extracted tree
passes ``tools/repo_audit.py``.  If sanitising would make two files
collide, a source-path hash suffix keeps them both.

Usage: python tools/extract_projects.py <archive.zip> [<dest-root>]
"""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

INVALID_CHARS = re.compile(r"[^A-Za-z0-9._-]")
ARCHIVE_SUFFIXES = ("-master", "-main")


def sanitize_component(name: str) -> str:
    """Make a single path component safe for the hygiene rules."""
    cleaned = INVALID_CHARS.sub("-", name).strip("-.")
    return cleaned or "unnamed"


def source_hash(source_path: str) -> str:
    """Stable short hash of the archive-internal source path."""
    return hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:8]


def project_dir_name(top_component: str) -> str:
    """Derive the project directory name from the archive's top component."""
    name = top_component
    lowered = name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return sanitize_component(name)


def _unique_path(path: Path, source_path: str) -> Path:
    """Return a non-clobbering variant of ``path`` if it already exists."""
    if not path.exists():
        return path
    suffix = source_hash(source_path)
    candidate = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{suffix}-{counter}{path.suffix}")
        counter += 1
    return candidate


def extract_zip(zip_path: Path, dest_root: Path) -> dict[str, Path]:
    """Extract every project in ``zip_path`` under ``dest_root``.

    Returns a mapping of archive top-level component -> project directory.
    """
    zip_path = Path(zip_path)
    dest_root = Path(dest_root)
    mapping: dict[str, Path] = {}

    with zipfile.ZipFile(zip_path) as archive:
        tops: dict[str, list[zipfile.ZipInfo]] = {}
        for info in archive.infolist():
            parts = [p for p in info.filename.split("/") if p not in ("", ".")]
            if not parts:
                continue
            tops.setdefault(parts[0], []).append(info)

        for top in sorted(tops):
            target_name = project_dir_name(top)
            project_dir = dest_root / target_name
            if project_dir.exists():
                project_dir = project_dir.with_name(
                    f"{target_name}-{source_hash(top)}"
                )
                counter = 2
                while project_dir.exists():
                    project_dir = project_dir.with_name(
                        f"{target_name}-{source_hash(top)}-{counter}"
                    )
                    counter += 1
            mapping[top] = project_dir

            for info in tops[top]:
                parts = [p for p in info.filename.split("/") if p not in ("", ".")]
                rel_parts = [sanitize_component(p) for p in parts[1:]]
                if not rel_parts:
                    continue  # the top-level directory entry itself
                if info.is_dir():
                    (project_dir / Path(*rel_parts)).mkdir(parents=True, exist_ok=True)
                    continue
                target = _unique_path(project_dir / Path(*rel_parts), info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src:
                    target.write_bytes(src.read())
    return mapping


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    zip_path = Path(argv[0])
    dest_root = Path(argv[1]) if len(argv) > 1 else Path(".")
    mapping = extract_zip(zip_path, dest_root)
    for top, project_dir in mapping.items():
        print(f"{top} -> {project_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
