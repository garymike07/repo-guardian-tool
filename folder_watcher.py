"""
Watches config.WATCH_FOLDER (e.g. C:\\Users\\ADMIN\\OneDrive\\Desktop\\sites)
for file changes. Each immediate subfolder is treated as one "project".

Changes are debounced (default 15s of quiet time) per project before firing
a callback, so saving 20 files in a row triggers ONE event, not 20.
"""
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config

log = logging.getLogger("repo_guardian.watcher")

IGNORE_DIR_NAMES = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".turbo", ".vercel"}


class _ProjectDebouncer:
    """Collects changed file paths for one project and fires on_settle after a quiet period."""

    def __init__(self, project_name: str, on_settle, quiet_seconds: float = 15.0):
        self.project_name = project_name
        self.on_settle = on_settle
        self.quiet_seconds = quiet_seconds
        self._changed_paths: set[str] = set()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def touch(self, path: str) -> None:
        with self._lock:
            self._changed_paths.add(path)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.quiet_seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            paths = sorted(self._changed_paths)
            self._changed_paths.clear()
        if paths:
            try:
                self.on_settle(self.project_name, paths)
            except Exception as e:  # noqa: BLE001
                log.error("Error handling settled changes for %s: %s", self.project_name, e)


class _Handler(FileSystemEventHandler):
    def __init__(self, watch_root: Path, on_settle):
        self.watch_root = watch_root
        self.on_settle = on_settle
        self._debouncers: dict[str, _ProjectDebouncer] = {}

    def _project_for(self, path: str) -> str | None:
        try:
            rel = Path(path).relative_to(self.watch_root)
        except ValueError:
            return None
        parts = rel.parts
        if not parts:
            return None
        if any(part in IGNORE_DIR_NAMES for part in parts):
            return None
        return parts[0]

    def _record(self, path: str) -> None:
        project = self._project_for(path)
        if not project:
            return
        deb = self._debouncers.setdefault(
            project, _ProjectDebouncer(project, self.on_settle)
        )
        deb.touch(path)

    def on_modified(self, event):
        if not event.is_directory:
            self._record(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._record(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._record(event.src_path)


def start(on_settle) -> Observer:
    """
    on_settle(project_name: str, changed_paths: list[str]) is called once
    changes to a project go quiet for a few seconds.
    """
    watch_root = config.WATCH_FOLDER
    if not watch_root.exists():
        log.error("Watch folder does not exist: %s", watch_root)
        raise SystemExit(1)

    handler = _Handler(watch_root, on_settle)
    observer = Observer()
    observer.schedule(handler, str(watch_root), recursive=True)
    observer.start()
    log.info("Watching %s for changes (per-subfolder = per-project)", watch_root)
    return observer
