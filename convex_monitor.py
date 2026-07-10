"""
Tails `npx convex logs --prod` for each configured Convex project (folder
resolved as WATCH_FOLDER/<folder>), watching for error lines in real time.

This requires the local Convex CLI to be logged in once per project
(`npx convex login` inside that folder) — deploy keys authenticate deploys,
not the interactive logs command, so this is separate from deploy.py.
"""
import logging
import re
import subprocess
import threading

import config
from notifier import notify

log = logging.getLogger("repo_guardian.convex")

ERROR_PATTERN = re.compile(r"\[ERROR\]|Uncaught|ArgumentValidationError|unauthorized", re.IGNORECASE)


def _tail_project(name: str, folder: str) -> None:
    path = config.WATCH_FOLDER / folder
    if not (path / "convex").exists():
        log.warning("No convex/ directory found for project '%s' at %s — skipping log tail", name, path)
        return

    cmd = ["npx", "convex", "logs", "--prod"]
    log.info("Tailing Convex logs for %s", name)
    try:
        proc = subprocess.Popen(cmd, cwd=str(path), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError:
        log.error("npx not found — is Node.js installed and on PATH? Skipping %s", name)
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line and ERROR_PATTERN.search(line):
            notify(f"Convex error — {name}", line[:250], category="convex")
            log.warning("[%s] %s", name, line)


def start_all() -> list[threading.Thread]:
    threads = []
    for project in config.CONVEX_PROJECTS:
        t = threading.Thread(target=_tail_project, args=(project["name"], project.get("folder", project["name"])),
                              daemon=True, name=f"convex-{project['name']}")
        t.start()
        threads.append(t)
    return threads
