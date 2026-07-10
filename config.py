"""
Loads credentials.json (gitignored, local-only). Never commit this file
anywhere, and never copy its contents into a project folder that Git tracks.
"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CRED_FILE = BASE_DIR / "credentials.json"
STATE_FILE = BASE_DIR / "state.json"

if not CRED_FILE.exists():
    print(f"ERROR: {CRED_FILE} not found.\n"
          f"Copy credentials.example.json to credentials.json and fill in your real values.")
    sys.exit(1)

with open(CRED_FILE, "r", encoding="utf-8-sig") as f:
    _raw = json.load(f)

WATCH_FOLDER = Path(_raw.get("watch_folder", ""))
GITHUB_ACCOUNTS = _raw.get("github_accounts", [])          # [{label, username, token}]
VERCEL_ACCOUNTS = _raw.get("vercel_accounts", [])           # [{label, email, token}]
CONVEX_PROJECTS = _raw.get("convex_projects", [])           # [{name, folder, cloud_url, deploy_key}]

POLL_INTERVAL_MINUTES = float(_raw.get("poll_interval_minutes", 5))
APPLY_CHANGES = bool(_raw.get("apply_changes", False))
COMMIT_MODE = _raw.get("commit_mode", "pr")                 # "pr" (safe, default) or "direct"
OPENCODE_MODEL = _raw.get("opencode_model", "")              # blank = opencode's default free model

DASHBOARD_PORT = int(_raw.get("dashboard_port", 47591))
SHOW_DASHBOARD_ON_START = bool(_raw.get("show_dashboard_on_start", False))

# username (lowercased) -> token, for quick lookup when we know which account owns a repo
GITHUB_TOKEN_BY_USER = {a["username"].lower(): a["token"] for a in GITHUB_ACCOUNTS if a.get("username")}


def validate() -> list[str]:
    problems = []
    if not GITHUB_ACCOUNTS:
        problems.append("No github_accounts configured in credentials.json")
    if not WATCH_FOLDER or not str(WATCH_FOLDER).strip():
        problems.append("watch_folder is not set in credentials.json")
    elif not WATCH_FOLDER.exists():
        problems.append(f"watch_folder does not exist on disk: {WATCH_FOLDER}")
    if not VERCEL_ACCOUNTS:
        problems.append("No vercel_accounts configured — Vercel monitoring/deploy will be skipped")
    if not CONVEX_PROJECTS:
        problems.append("No convex_projects configured — Convex monitoring/deploy will be skipped")
    if COMMIT_MODE not in ("pr", "direct"):
        problems.append(f"commit_mode should be 'pr' or 'direct', got: {COMMIT_MODE!r}")
    return problems


def convex_project_by_folder(folder_name: str) -> dict | None:
    for p in CONVEX_PROJECTS:
        if p.get("folder", "").lower() == folder_name.lower():
            return p
    return None
