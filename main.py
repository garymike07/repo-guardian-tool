"""
repo-guardian v2 — local Windows watcher + auto-deploy for GitHub, Vercel, Convex.

Run:   python main.py
Stop:  right-click tray icon -> Exit, or Ctrl+C.

Primary trigger is the LOCAL FOLDER WATCHER on config.WATCH_FOLDER: when you
save changes in a project subfolder, after a short quiet period it:
  1. Notifies you what changed (Windows toast + dashboard window)
  2. Refreshes that project's AGENTS.md (env var names + workflow, no secrets)
  3. If apply_changes=true: commits (AI-drafted message via OpenCode) and
     either pushes to a branch + opens a PR (commit_mode="pr", default) or
     pushes straight to the default branch (commit_mode="direct")
  4. If apply_changes=true and convex/ changed: runs `npx convex deploy`
  5. If apply_changes=true and the folder matches a linked Vercel project:
     runs a direct `vercel --prod` deploy

A slower background loop also polls GitHub (new commits pushed from
elsewhere) and Vercel (deployment status, e.g. from Vercel's own Git-push
auto-deploy) so you're notified even for changes made outside this laptop.
Convex errors are tailed live and notified immediately, independent of the
poll cycle.

The tray icon's "Open Dashboard" shows a real native desktop window (via
pywebview / Windows' built-in WebView2 — no browser chrome, no address bar)
with a live feed of everything above. It's movable and resizable like any
other window, and closing it just hides it — repo-guardian keeps running.
"""
import logging
import logging.handlers
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import config
import dashboard
import github_manager
import vercel_monitor
import convex_monitor
import folder_watcher
import agents_md
import git_ops
import deploy
import state
from notifier import notify

try:
    import webview  # pywebview — renders the dashboard as a real OS window
    _HAS_WEBVIEW = True
except ImportError:
    _HAS_WEBVIEW = False

LOG_DIR = config.BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

_handlers = [
    logging.handlers.RotatingFileHandler(
        LOG_DIR / "repo_guardian.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    ),
]
if sys.stdout is not None:  # pythonw.exe (used for silent autostart) has no stdout — skip it then
    _handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("repo_guardian.main")
_stop_event = threading.Event()

# Only one repo-guardian instance should ever run at once (autostart + a manual
# launch could otherwise both fire duplicate deploys). We hold a bound socket
# for the lifetime of the process as the lock; second instance detects it's
# already taken and exits immediately.
_LOCK_PORT = 47590
_lock_socket: socket.socket | None = None


def _acquire_single_instance_lock() -> bool:
    global _lock_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind(("127.0.0.1", _LOCK_PORT))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _lock_socket = s  # keep alive/open for as long as this process runs
    return True


def _dashboard_url() -> str:
    return f"http://127.0.0.1:{config.DASHBOARD_PORT}/"


# --- Native window state (used only when pywebview is available) -----------
_webview_window = None
_shutting_down = False


def _on_window_closing() -> bool:
    """Clicking the window's X hides it instead of quitting — repo-guardian
    keeps running in the background, exactly like minimizing to tray."""
    if _shutting_down:
        return True  # real shutdown in progress (tray Exit) — allow it
    if _webview_window is not None:
        _webview_window.hide()
    return False  # veto the close


# --- Fallback path if pywebview isn't installed: open a real browser -------
_BROWSER_CANDIDATES = [
    "msedge", "chrome",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _find_app_mode_browser() -> str | None:
    for candidate in _BROWSER_CANDIDATES:
        if candidate.lower().endswith(".exe"):
            if Path(candidate).exists():
                return candidate
        else:
            path = shutil.which(candidate)
            if path:
                return path
    return None


def open_dashboard_window(icon=None, item=None) -> None:
    """Show the native dashboard window. Falls back to a chromeless app-style
    browser window, then a normal browser tab, if pywebview isn't installed."""
    if _HAS_WEBVIEW and _webview_window is not None:
        _webview_window.show()
        return
    url = _dashboard_url()
    browser = _find_app_mode_browser()
    if browser:
        try:
            subprocess.Popen([browser, f"--app={url}", "--window-size=480,780"])
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Could not launch browser in app mode: %s", e)
    webbrowser.open(url)

# cache of GitHub full_name per local folder, populated lazily
_repo_cache: dict[str, str] = {}
# cache of Vercel (token, project) per local folder name
_vercel_cache: dict[str, tuple] = {}


def _resolve_github_full_name(project_name: str) -> str | None:
    if project_name in _repo_cache:
        return _repo_cache[project_name]
    project_path = config.WATCH_FOLDER / project_name
    origin = git_ops.get_origin_info(project_path)
    if origin:
        full_name = f"{origin[0]}/{origin[1]}"
        _repo_cache[project_name] = full_name
        return full_name
    return None


def _resolve_vercel_target(project_name: str):
    if project_name in _vercel_cache:
        return _vercel_cache[project_name]
    for account in config.VERCEL_ACCOUNTS:
        for project in vercel_monitor.list_projects(account["token"]):
            if project["name"].lower() == project_name.lower():
                _vercel_cache[project_name] = (account["token"], project["name"])
                return _vercel_cache[project_name]
    _vercel_cache[project_name] = None
    return None


def on_project_changed(project_name: str, changed_paths: list[str]) -> None:
    project_path = config.WATCH_FOLDER / project_name
    rel_paths = [p.replace(str(config.WATCH_FOLDER), "").lstrip("\\/") for p in changed_paths]
    preview = ", ".join(rel_paths[:5]) + ("..." if len(rel_paths) > 5 else "")
    log.info("Changes settled for %s: %s", project_name, preview)

    github_full_name = _resolve_github_full_name(project_name)

    # 1. Refresh AGENTS.md (safe, always runs — no secrets written)
    try:
        agents_md.write(project_path, project_name, github_full_name)
    except Exception as e:  # noqa: BLE001
        log.error("AGENTS.md refresh failed for %s: %s", project_name, e)

    if not config.APPLY_CHANGES:
        notify(f"{project_name}: changes detected",
               f"{len(rel_paths)} file(s) changed: {preview}\n(dry run — set apply_changes=true to auto-deploy)",
               category="folder")
        return

    # 2. Commit + push/PR
    result = git_ops.commit_and_publish(project_path, changed_paths)
    if result["committed"] and result["pushed"]:
        if result["pr_url"]:
            notify(f"{project_name}: PR opened", "Review and merge when ready.", url=result["pr_url"], category="github")
        else:
            notify(f"{project_name}: pushed to {result['branch']}", preview, category="github")
    elif result["committed"] and not result["pushed"]:
        notify(f"{project_name}: commit ok, push FAILED", "Check credentials.json token permissions.", category="github")

    # 3. Convex deploy if convex/ changed
    if deploy.has_convex_changes(changed_paths):
        convex_project = config.convex_project_by_folder(project_name)
        if convex_project:
            dep_result = deploy.deploy_convex(project_path, convex_project["deploy_key"])
            if dep_result["success"]:
                notify(f"{project_name}: Convex deployed", "Backend deploy succeeded.", category="convex")
            else:
                notify(f"{project_name}: Convex deploy FAILED", dep_result["output"][-200:], category="convex")

    # 4. Vercel deploy if this folder is a linked Vercel project
    target = _resolve_vercel_target(project_name)
    if target:
        token, _ = target
        dep_result = deploy.deploy_vercel(project_path, token)
        if dep_result["success"]:
            notify(f"{project_name}: Vercel deployed", "Deploy succeeded.", url=dep_result.get("url"), category="vercel")
        else:
            notify(f"{project_name}: Vercel deploy FAILED", dep_result["output"][-200:], category="vercel")


def github_poll_cycle() -> None:
    try:
        repos = github_manager.list_all_repos()
    except Exception as e:  # noqa: BLE001
        log.error("GitHub repo listing failed: %s", e)
        return

    last_shas = state.get("github_last_sha", {})
    for repo in repos:
        full_name = repo["full_name"]
        branch = repo.get("default_branch", "main")
        sha = github_manager.get_latest_commit_sha(full_name, branch, repo["_token"])
        if not sha or last_shas.get(full_name) == sha:
            continue
        is_first_check = full_name not in last_shas
        last_shas[full_name] = sha
        if is_first_check:
            continue  # don't fire a notification just for establishing baseline on first run

        notify(f"GitHub: {full_name}", f"New commit on {branch} ({sha[:7]})", category="github")

        try:
            summary = github_manager.remote_cleanup_pass(repo)
            if summary["pr_url"]:
                notify(f"repo-guardian: {full_name}", "Cleanup/README PR opened", url=summary["pr_url"], category="github")
        except Exception as e:  # noqa: BLE001
            log.error("Remote cleanup pass failed for %s: %s", full_name, e)

    state.set_key("github_last_sha", last_shas)


def vercel_poll_cycle() -> None:
    try:
        events = vercel_monitor.check_all_accounts()
    except Exception as e:  # noqa: BLE001
        log.error("Vercel check failed: %s", e)
        return
    for ev in events:
        status = (ev["status"] or "unknown").upper()
        title = f"Vercel [{ev['account_label']}]: {ev['project_name']} — {status}"
        if status in ("ERROR", "CANCELED"):
            notify(title, "Deployment failed — tap to inspect.", url=ev.get("inspector_url") or ev.get("url"), category="vercel")
        elif status == "READY":
            notify(title, "Deployed successfully.", url=ev.get("url"), category="vercel")
        else:
            notify(title, f"Status changed to {status}.", category="vercel")


def poll_loop() -> None:
    interval_sec = max(30, int(config.POLL_INTERVAL_MINUTES * 60))
    while not _stop_event.is_set():
        log.info("--- poll cycle start ---")
        github_poll_cycle()
        vercel_poll_cycle()
        log.info("--- poll cycle done, sleeping %ss ---", interval_sec)
        _stop_event.wait(interval_sec)


def bootstrap_agents_md() -> None:
    """Write/refresh AGENTS.md for every existing project folder at startup."""
    if not config.WATCH_FOLDER.exists():
        return
    for entry in config.WATCH_FOLDER.iterdir():
        if entry.is_dir() and not entry.name.startswith("."):
            github_full_name = _resolve_github_full_name(entry.name)
            try:
                agents_md.write(entry, entry.name, github_full_name)
            except Exception as e:  # noqa: BLE001
                log.error("Initial AGENTS.md write failed for %s: %s", entry.name, e)


def _build_tray_icon():
    """Returns a configured pystray.Icon, or None if pystray/Pillow aren't installed."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    img = Image.new("RGB", (64, 64), "black")
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill="lime")

    def on_exit(icon, item):
        global _shutting_down
        _shutting_down = True
        _stop_event.set()
        icon.stop()
        if _HAS_WEBVIEW and _webview_window is not None:
            _webview_window.destroy()

    return pystray.Icon(
        "repo-guardian", img, "repo-guardian",
        menu=pystray.Menu(
            pystray.MenuItem("Open Dashboard", open_dashboard_window, default=True),
            pystray.MenuItem("Exit", on_exit),
        ),
    )


def main() -> None:
    global _webview_window

    if not _acquire_single_instance_lock():
        log.warning("repo-guardian is already running (lock port %s in use) — exiting this copy.",
                    _LOCK_PORT)
        return

    problems = config.validate()
    for p in problems:
        log.warning("Config issue: %s", p)

    log.info("apply_changes=%s  commit_mode=%s", config.APPLY_CHANGES, config.COMMIT_MODE)

    threading.Thread(target=dashboard.run, kwargs={"port": config.DASHBOARD_PORT}, daemon=True).start()
    time.sleep(0.4)  # give the dashboard server a moment to bind before a window tries to load it
    log.info("Dashboard running at %s", _dashboard_url())

    bootstrap_agents_md()
    observer = folder_watcher.start(on_project_changed)

    if config.CONVEX_PROJECTS:
        convex_monitor.start_all()

    notify("repo-guardian started", f"Watching {config.WATCH_FOLDER}")

    threading.Thread(target=poll_loop, daemon=True).start()

    tray_icon = _build_tray_icon()
    if tray_icon is None:
        log.info("pystray/Pillow not installed — no tray icon. Dashboard: %s", _dashboard_url())
    else:
        threading.Thread(target=tray_icon.run, daemon=True).start()

    if _HAS_WEBVIEW:
        # Native desktop window (WebView2) — movable, resizable, no browser chrome.
        # Shown immediately if configured to, or if there's no tray icon to reopen it from.
        start_visible = config.SHOW_DASHBOARD_ON_START or tray_icon is None
        _webview_window = webview.create_window(
            "repo-guardian", _dashboard_url(),
            width=480, height=780, resizable=True, hidden=not start_visible,
        )
        _webview_window.events.closing += _on_window_closing
    else:
        log.info("pywebview not installed — 'Open Dashboard' will use your browser instead. "
                  "For a native window: pip install pywebview")
        if config.SHOW_DASHBOARD_ON_START:
            threading.Timer(2.0, open_dashboard_window).start()

    try:
        if _HAS_WEBVIEW:
            webview.start(debug=False)  # blocks here on the main thread until the window is destroyed
        else:
            _stop_event.wait()  # nothing else needs the main thread — just keep the process alive
    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        observer.stop()
        observer.join(timeout=5)
        if tray_icon is not None:
            tray_icon.stop()
        log.info("repo-guardian stopped.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("repo-guardian crashed — see traceback above/in logs/repo_guardian.log")
        raise
