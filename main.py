"""
repo-guardian v2 — local Windows watcher + auto-deploy for GitHub, Vercel, Convex.

Run:   python main.py
Stop:  right-click tray icon -> Exit, or Ctrl+C.

Primary trigger is the LOCAL FOLDER WATCHER on config.WATCH_FOLDER: when you
save changes in a project subfolder, after a short quiet period it:
  1. Notifies you what changed (Windows toast)
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
"""
import logging
import logging.handlers
import sys
import threading

import config
import github_manager
import vercel_monitor
import convex_monitor
import folder_watcher
import agents_md
import git_ops
import deploy
import state
from notifier import notify

LOG_DIR = config.BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "repo_guardian.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        ),
    ],
)
log = logging.getLogger("repo_guardian.main")
_stop_event = threading.Event()

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
               f"{len(rel_paths)} file(s) changed: {preview}\n(dry run — set apply_changes=true to auto-deploy)")
        return

    # 2. Commit + push/PR
    result = git_ops.commit_and_publish(project_path, changed_paths)
    if result["committed"] and result["pushed"]:
        if result["pr_url"]:
            notify(f"{project_name}: PR opened", "Review and merge when ready.", url=result["pr_url"])
        else:
            notify(f"{project_name}: pushed to {result['branch']}", preview)
    elif result["committed"] and not result["pushed"]:
        notify(f"{project_name}: commit ok, push FAILED", "Check credentials.json token permissions.")

    # 3. Convex deploy if convex/ changed
    if deploy.has_convex_changes(changed_paths):
        convex_project = config.convex_project_by_folder(project_name)
        if convex_project:
            dep_result = deploy.deploy_convex(project_path, convex_project["deploy_key"])
            if dep_result["success"]:
                notify(f"{project_name}: Convex deployed", "Backend deploy succeeded.")
            else:
                notify(f"{project_name}: Convex deploy FAILED", dep_result["output"][-200:])

    # 4. Vercel deploy if this folder is a linked Vercel project
    target = _resolve_vercel_target(project_name)
    if target:
        token, _ = target
        dep_result = deploy.deploy_vercel(project_path, token)
        if dep_result["success"]:
            notify(f"{project_name}: Vercel deployed", "Deploy succeeded.", url=dep_result.get("url"))
        else:
            notify(f"{project_name}: Vercel deploy FAILED", dep_result["output"][-200:])


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

        notify(f"GitHub: {full_name}", f"New commit on {branch} ({sha[:7]})")

        try:
            summary = github_manager.remote_cleanup_pass(repo)
            if summary["pr_url"]:
                notify(f"repo-guardian: {full_name}", "Cleanup/README PR opened", url=summary["pr_url"])
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
            notify(title, "Deployment failed — tap to inspect.", url=ev.get("inspector_url") or ev.get("url"))
        elif status == "READY":
            notify(title, "Deployed successfully.", url=ev.get("url"))
        else:
            notify(title, f"Status changed to {status}.")


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


def start_tray_icon() -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        log.info("pystray/Pillow not installed — running without a tray icon (console only).")
        poll_loop()
        return

    img = Image.new("RGB", (64, 64), "black")
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill="lime")

    def on_exit(icon, item):
        _stop_event.set()
        icon.stop()

    icon = pystray.Icon("repo-guardian", img, "repo-guardian",
                         menu=pystray.Menu(pystray.MenuItem("Exit", on_exit)))
    threading.Thread(target=poll_loop, daemon=True).start()
    icon.run()


def main() -> None:
    problems = config.validate()
    for p in problems:
        log.warning("Config issue: %s", p)

    log.info("apply_changes=%s  commit_mode=%s", config.APPLY_CHANGES, config.COMMIT_MODE)

    bootstrap_agents_md()

    observer = folder_watcher.start(on_project_changed)

    if config.CONVEX_PROJECTS:
        convex_monitor.start_all()

    notify("repo-guardian started", f"Watching {config.WATCH_FOLDER}")

    try:
        start_tray_icon()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        observer.stop()
        observer.join(timeout=5)
        log.info("repo-guardian stopped.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("repo-guardian crashed — see traceback above/in logs/repo_guardian.log")
        raise
