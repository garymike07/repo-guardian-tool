"""
Scans a project folder's source code for environment variable *references*
(names only — never values) so we can tell you and your coding agents which
vars a given project actually needs, and cross-check that against what's
configured centrally.

Patterns covered: process.env.X / process.env["X"] (JS/TS),
import.meta.env.X (Vite), os.getenv("X") / os.environ["X"] / os.environ.get("X") (Python).
"""
import re
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", "__pycache__", ".venv", "venv"}
SOURCE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".py", ".mjs", ".cjs"}

PATTERNS = [
    re.compile(r"process\.env\.([A-Z0-9_]+)"),
    re.compile(r"process\.env\[[\"']([A-Z0-9_]+)[\"']\]"),
    re.compile(r"import\.meta\.env\.([A-Z0-9_]+)"),
    re.compile(r"os\.getenv\([\"']([A-Z0-9_]+)[\"']"),
    re.compile(r"os\.environ\[[\"']([A-Z0-9_]+)[\"']\]"),
    re.compile(r"os\.environ\.get\([\"']([A-Z0-9_]+)[\"']"),
]


def scan(project_path: Path) -> set[str]:
    """Returns the set of env var NAMES referenced anywhere in the project's source."""
    found: set[str] = set()

    for path in project_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in SOURCE_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in PATTERNS:
            found.update(pattern.findall(text))

    # Also pick up names declared in a .env.example, if present (values ignored)
    example = project_path / ".env.example"
    if example.exists():
        try:
            for line in example.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    found.add(line.split("=", 1)[0].strip())
        except OSError:
            pass

    return found
