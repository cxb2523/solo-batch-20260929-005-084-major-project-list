"""Build the project index for README.md and index.html.

Discovers top-level project directories, infers each project's language
and category from its README and entry files, then rewrites the marked
section of README.md and regenerates index.html.  The script is
idempotent: running it twice leaves both files byte-identical.

Usage: python tools/build_index.py [repo-root]
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

BEGIN_MARKER = "<!-- PROJECT-INDEX:BEGIN -->"
END_MARKER = "<!-- PROJECT-INDEX:END -->"

EXCLUDED_DIRS = {"tools", "tests", "__pycache__"}

ENTRY_FILE_NAMES = {
    "main.py", "app.py", "manage.py", "index.html", "package.json",
    "pyproject.toml", "setup.py", "cargo.toml", "go.mod", "makefile",
    "gemfile", "pom.xml", "build.gradle",
}

EXT_TO_LANGUAGE = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".java": "Java", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".go": "Go", ".rb": "Ruby", ".rs": "Rust",
    ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin", ".sh": "Shell",
    ".lua": "Lua", ".r": "R", ".html": "HTML", ".css": "CSS",
}

LANGUAGE_NAMES = sorted(set(EXT_TO_LANGUAGE.values()))

# Names that collide with common English words; they are only trusted when
# detected from file extensions, not from free-text README mentions.
AMBIGUOUS_LANGUAGE_NAMES = {"C", "Go", "R", "Shell"}

CATEGORIES = [
    ("Algorithms & Numbers",
     ["algorithm", "number", "prime", "fibonacci", "sort", "math",
      "calculator", "equation", "graph", "digit"]),
    ("Web & Networking",
     ["web", "http", "server", "client", "network", "socket", "api",
      "browser", "url", "site"]),
    ("Text & Language",
     ["text", "string", "regex", "parser", "markdown", "word", "spell",
      "cipher"]),
    ("Games & Graphics",
     ["game", "player", "sprite", "canvas", "puzzle", "graphics"]),
    ("Data & Files",
     ["data", "database", "sql", "file", "csv", "json", "xml", "backup",
      "image"]),
    ("Utilities & Tools",
     ["tool", "utility", "converter", "generator", "editor", "clock",
      "alarm"]),
]
FALLBACK_CATEGORY = "Miscellaneous"


def is_project_dir(path: Path) -> bool:
    names = {p.name.lower() for p in path.iterdir() if p.is_file()}
    if any(n.startswith("readme") for n in names):
        return True
    return bool(names & ENTRY_FILE_NAMES)


def discover_projects(root: Path) -> list[Path]:
    projects = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in EXCLUDED_DIRS or child.name.startswith("."):
            continue
        if is_project_dir(child):
            projects.append(child)
    return projects


def read_readme(project_dir: Path) -> str:
    for child in sorted(project_dir.iterdir(), key=lambda p: p.name.lower()):
        if child.is_file() and child.name.lower().startswith("readme"):
            return child.read_text(encoding="utf-8", errors="replace")
    return ""


def _top_counts(counts: dict[str, int]) -> str:
    return max(sorted(counts), key=lambda name: counts[name])


def infer_language(project_dir: Path, readme_text: str) -> str:
    counts: dict[str, int] = {}
    for path in project_dir.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        language = EXT_TO_LANGUAGE.get(path.suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    if counts:
        return _top_counts(counts)
    mentions: dict[str, int] = {}
    for language in LANGUAGE_NAMES:
        if language in AMBIGUOUS_LANGUAGE_NAMES:
            continue
        hits = len(re.findall(r"\b" + re.escape(language) + r"\b",
                              readme_text))
        if hits >= 2:
            # a single stray mention (e.g. a product name) is not evidence
            mentions[language] = hits
    if mentions:
        return _top_counts(mentions)
    return "Docs"


def infer_category(project_dir: Path, readme_text: str) -> str:
    entry_names = " ".join(p.name for p in project_dir.iterdir() if p.is_file())
    text = f"{readme_text}\n{entry_names}".lower()
    best_category, best_score = FALLBACK_CATEGORY, 0
    for category, keywords in CATEGORIES:
        score = sum(
            len(re.findall(r"\b" + re.escape(keyword) + r"\b", text))
            for keyword in keywords
        )
        if score > best_score:
            best_category, best_score = category, score
    return best_category


def infer_description(readme_text: str) -> str:
    lines = readme_text.splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or line.startswith(("#", "!", "[!", "<", "|")):
            continue
        if re.fullmatch(r"[=-]+", line):
            continue  # setext heading underline
        if index + 1 < len(lines) and re.fullmatch(r"[=-]+", lines[index + 1].strip()):
            continue  # setext heading text
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = line.strip(" -")
        if not line:
            continue
        if len(line) > 100:
            line = line[:97].rstrip() + "..."
        return line
    return ""


def group_projects(projects: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for project in projects:
        groups.setdefault(project["category"], []).append(project)
    ordered = []
    seen = set()
    for category, _keywords in CATEGORIES:
        if category in groups:
            ordered.append((category, groups[category]))
            seen.add(category)
    for category in sorted(groups):
        if category not in seen:
            ordered.append((category, groups[category]))
    return ordered


def render_markdown_section(grouped: list[tuple[str, list[dict]]]) -> str:
    lines = [
        BEGIN_MARKER,
        "",
        "## Project Index",
        "",
        "_Generated by `tools/build_index.py` - do not edit by hand._",
        "",
    ]
    for category, projects in grouped:
        lines.append(f"### {category}")
        lines.append("")
        for project in projects:
            description = project["description"]
            suffix = f" - {description}" if description else ""
            lines.append(
                f"- [{project['name']}]({project['name']}/) - "
                f"{project['language']}{suffix}"
            )
        lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines)


def update_readme(readme_path: Path, section: str) -> None:
    if readme_path.exists():
        text = readme_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    else:
        text = ""
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + ".*?" + re.escape(END_MARKER), re.DOTALL
    )
    if pattern.search(text):
        new_text = pattern.sub(lambda _match: section, text)
    else:
        base = text.rstrip("\n")
        new_text = f"{base}\n\n{section}" if base else section
    readme_path.write_text(
        new_text.rstrip("\n") + "\n", encoding="utf-8", newline="\n"
    )


HTML_STYLE = """
body { font-family: system-ui, sans-serif; margin: 0; background: #f6f8fa; }
main { max-width: 760px; margin: 2rem auto; padding: 0 1rem; }
h1 { border-bottom: 2px solid #d0d7de; padding-bottom: .3rem; }
section { background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
          padding: .5rem 1.25rem 1rem; margin: 1rem 0; }
ul { padding-left: 1.25rem; }
li { margin: .4rem 0; }
.lang { background: #ddf4ff; border-radius: 10px; padding: .1rem .55rem;
        font-size: .85em; color: #0550ae; }
.note { color: #57606a; }
""".strip("\n")


def render_html(grouped: list[tuple[str, list[dict]]]) -> str:
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Project Index</title>",
        "<style>",
        HTML_STYLE,
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<h1>Project Index</h1>",
        '<p class="note">Generated by <code>tools/build_index.py</code>.</p>',
    ]
    for category, projects in grouped:
        parts.append("<section>")
        parts.append(f"<h2>{html.escape(category)}</h2>")
        parts.append("<ul>")
        for project in projects:
            name = html.escape(project["name"])
            language = html.escape(project["language"])
            description = html.escape(project["description"])
            suffix = f" - {description}" if description else ""
            parts.append(
                f'<li><a href="{name}/">{name}</a> '
                f'<span class="lang">{language}</span>{suffix}</li>'
            )
        parts.append("</ul>")
        parts.append("</section>")
    parts.extend(["</main>", "</body>", "</html>", ""])
    return "\n".join(parts)


def main(root=None) -> int:
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    projects = []
    for project_dir in discover_projects(root):
        readme_text = read_readme(project_dir)
        projects.append({
            "name": project_dir.name,
            "language": infer_language(project_dir, readme_text),
            "category": infer_category(project_dir, readme_text),
            "description": infer_description(readme_text),
        })
    grouped = group_projects(projects)
    update_readme(root / "README.md", render_markdown_section(grouped))
    (root / "index.html").write_text(
        render_html(grouped), encoding="utf-8", newline="\n"
    )
    print(f"indexed {len(projects)} project(s) into README.md and index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
