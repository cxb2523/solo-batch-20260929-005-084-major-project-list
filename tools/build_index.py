#!/usr/bin/env python3
"""Build a categorised project index from the extracted projects.

Each top-level project directory is scanned for a README and a small set of
well-known entry/source files; the primary programming language and a project
category are inferred deterministically. Results are written:

  * into a marked region of README.md (regenerated on every run), and
  * as a standalone index.html (relative links, inline CSS, no external assets)

The script is idempotent: running it twice leaves both outputs byte-identical.
"""
from __future__ import annotations

import html
import re
import sys
from collections import OrderedDict
from pathlib import Path

START = "<!-- BEGIN PROJECT INDEX (auto-generated; do not edit) -->"
END = "<!-- END PROJECT INDEX -->"

SKIP_TOPLEVEL = {"tools", "tests", ".git", ".github", "__pycache__"}
README_NAMES = ("README.md", "README", "README.txt", "readme.md")

ENTRY_CANDIDATES = (
    "main.py", "app.py", "__main__.py", "setup.py", "pyproject.toml",
    "package.json", "index.js", "main.js", "app.js", "server.js",
    "index.ts", "main.ts", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "Makefile", "CMakeLists.txt", "composer.json",
    "Gemfile", "requirements.txt", "index.html", "Dockerfile",
    "CNAME",
)

EXT_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".r": "R",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".lua": "Lua",
    ".pl": "Perl",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".clj": "Clojure",
    ".hs": "Haskell",
}

FILENAME_LANGUAGE = {
    "Makefile": "Make",
    "Dockerfile": "Dockerfile",
    "package.json": "JavaScript",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "Gemfile": "Ruby",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "setup.py": "Python",
}

README_LANGUAGE_KEYWORDS = (
    ("python", "Python"),
    ("javascript", "JavaScript"),
    ("node.js", "JavaScript"),
    ("typescript", "TypeScript"),
    ("java", "Java"),
    ("c++", "C++"),
    ("golang", "Go"),
    (" rust", "Rust"),
    ("ruby", "Ruby"),
    ("php", "PHP"),
    ("swift", "Swift"),
    ("kotlin", "Kotlin"),
    ("sql", "SQL"),
    ("html", "HTML"),
)

CATEGORIES = (
    ("Numbers", ("fibonacci", "prime", "pi ", "factorial", "collatz",
                 "calculator", "number", "decimal", "exponent", "sieve")),
    ("Classic Algorithms", ("algorithm", "sorting", "sort ", "search",
                            "mergesort", "quicksort", "bubble sort")),
    ("Graph", ("graph", "eulerian", "node", "edge", "network from")),
    ("Data Structures", ("linked list", "hash", "stack", "queue",
                         "data structure", "tree", "heap")),
    ("Text", ("text", "string", "markdown", "piglatin", "palindrome",
              "parse", "lexer", "json", "csv")),
    ("Networking", ("network", "socket", "server", "client", "port",
                    "http request", "download")),
    ("Classes", ("class", "object-oriented", "inheritance", "bank account",
                 "oop")),
    ("Threading", ("thread", "concurrency", "parallel", "asynchronous",
                   "async ")),
    ("Web", ("web", "html", "website", "cname", "forum", "cms",
             "content management", "browser", "e-card", "captcha")),
    ("Files", ("file", "directory", "flickr", "thumbnail", "pdf",
               "zip file")),
    ("Databases", ("database", "sql", "db ", "query", "mysql", "postgres")),
    ("Graphics and Multimedia", ("graphic", "image", "png", "picture",
                                 "multimedia", "screenshot", "mp3",
                                 "animation", "video", "music", "gallery")),
    ("Security", ("security", "cipher", "encrypt", "decrypt", "password",
                  "caesar", "firewall", "ssl", "hash check")),
)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def find_readme(project: Path):
    for name in README_NAMES:
        candidate = project / name
        if candidate.is_file():
            return candidate
    return None


def find_entry(project: Path):
    for name in ENTRY_CANDIDATES:
        candidate = project / name
        if candidate.is_file():
            return candidate.relative_to(project).as_posix()
    return None


def language_for_file(path: Path):
    return FILENAME_LANGUAGE.get(path.name) or EXT_LANGUAGE.get(path.suffix.lower())


def infer_language(project: Path, readme_text: str, entry):
    scores: "OrderedDict[str, int]" = OrderedDict()

    def bump(lang, weight):
        if lang:
            scores[lang] = scores.get(lang, 0) + weight

    for child in sorted(project.rglob("*")):
        if not child.is_file() or ".git" in child.parts:
            continue
        bump(language_for_file(child), 1)
    if entry:
        bump(language_for_file(project / entry), 3)

    lowered = readme_text.lower()
    for keyword, lang in README_LANGUAGE_KEYWORDS:
        if keyword in lowered:
            bump(lang, 1)

    if not scores:
        return "Unknown"
    return max(sorted(scores), key=lambda lang: scores[lang])


def infer_category(readme_text: str, entry):
    haystack = readme_text.lower()
    if entry:
        haystack = f"{haystack}\n{entry.lower()}"
    scores: "OrderedDict[str, int]" = OrderedDict()
    for category, keywords in CATEGORIES:
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score:
            scores[category] = score
    if not scores:
        return "Uncategorized"
    return max(sorted(scores), key=lambda cat: scores[cat])


def describe(readme_text: str, name: str):
    for line in readme_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("!") or stripped.startswith("["):
            continue
        cleaned = re.sub(r"[#*`>\[\]]", "", stripped).strip()
        if len(cleaned) >= 3:
            return cleaned[:100]
    return f"Project {name}"


def discover_projects(root: Path):
    projects = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in SKIP_TOPLEVEL:
            continue
        readme = find_readme(child)
        entry = find_entry(child)
        if readme is None and entry is None:
            continue
        readme_text = read_text(readme) if readme else ""
        link_target = None
        if readme is not None:
            link_target = readme.relative_to(root).as_posix()
        elif entry is not None:
            link_target = f"{child.name}/{entry}"
        projects.append({
            "name": child.name,
            "readme": readme.relative_to(root).as_posix() if readme else None,
            "entry": entry,
            "language": infer_language(child, readme_text, entry),
            "category": infer_category(readme_text, entry),
            "description": (describe(readme_text, child.name)
                            if readme_text else f"Project {child.name}"),
            "link": link_target,
        })
    return projects


def group_by_category(projects):
    grouped: "OrderedDict[str, list]" = OrderedDict()
    for project in sorted(projects, key=lambda item: (item["category"],
                                                      item["name"])):
        grouped.setdefault(project["category"], []).append(project)
    return grouped


def build_markdown(grouped) -> str:
    lines = [
        START,
        "",
        "# Project Index",
        "",
        "Categorised inventory inferred from each project's README and entry "
        "files by `tools/build_index.py`.",
        "",
    ]
    for category, projects in grouped.items():
        lines.append(f"## {category}")
        lines.append("")
        lines.append("| Project | Language | Entry | Description |")
        lines.append("| --- | --- | --- | --- |")
        for project in projects:
            name = f"[{project['name']}]({project['link']})"
            entry = project["entry"] or "-"
            description = project["description"].replace("|", "\\|")
            lines.append(
                f"| {name} | {project['language']} | {entry} | {description} |"
            )
        lines.append("")
    lines.append(END)
    lines.append("")
    return "\n".join(lines)


def build_html(grouped) -> str:
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Project Index</title>",
        "<style>",
        "body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "margin:2rem auto;max-width:960px;padding:0 1rem;color:#1f2933;}",
        "h1{margin-bottom:.25rem}h2{margin-top:2rem;border-bottom:2px solid "
        "#e4e7eb;padding-bottom:.25rem}",
        "table{border-collapse:collapse;width:100%;margin:.5rem 0}",
        "th,td{border:1px solid #cbd2d9;padding:.4rem .6rem;text-align:left;"
        "vertical-align:top}",
        "th{background:#f5f7fa}code{background:#f0f4f8;padding:.1rem .3rem;"
        "border-radius:3px}",
        ".meta{color:#627d98;font-size:.9rem}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Project Index</h1>",
        '<p class="meta">Auto-generated by <code>tools/build_index.py</code>. '
        "Open this file directly in a browser.</p>",
    ]
    total = sum(len(items) for items in grouped.values())
    parts.append(f'<p class="meta">{total} project(s) in '
                 f"{len(grouped)} category/categories.</p>")
    for category, projects in grouped.items():
        parts.append(f"<h2>{html.escape(category)}</h2>")
        parts.append("<table>")
        parts.append("<thead><tr><th>Project</th><th>Language</th>"
                     "<th>Entry</th><th>Description</th></tr></thead>")
        parts.append("<tbody>")
        for project in projects:
            href = html.escape(project["link"], quote=True)
            parts.append(
                "<tr>"
                f'<td><a href="{href}">{html.escape(project["name"])}</a></td>'
                f'<td>{html.escape(project["language"])}</td>'
                f"<td><code>{html.escape(project['entry'] or '-')}</code></td>"
                f"<td>{html.escape(project['description'])}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    parts.append("")
    return "\n".join(parts)


def render_readme(original: str, block: str) -> str:
    normalized = original.replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(
        re.escape(START) + r".*?" + re.escape(END) + r"\n?", re.DOTALL
    )
    if pattern.search(normalized):
        updated = pattern.sub(block, normalized)
    else:
        updated = normalized.rstrip("\n") + "\n\n" + block
    if not updated.endswith("\n"):
        updated += "\n"
    return updated


def build(root: Path) -> tuple[Path, Path]:
    grouped = group_by_category(discover_projects(root))
    readme_path = root / "README.md"
    original = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    block = build_markdown(grouped)
    readme_path.write_text(render_readme(original, block), encoding="utf-8",
                           newline="\n")
    (root / "index.html").write_text(build_html(grouped), encoding="utf-8",
                                     newline="\n")
    return readme_path, root / "index.html"


def main(argv=None) -> int:
    root = Path(__file__).resolve().parent.parent
    readme_path, index_path = build(root)
    print(f"updated {readme_path} and {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
