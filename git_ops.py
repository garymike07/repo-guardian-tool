"""
Runs real `git` commands inside a watched project folder to commit and push
local changes, then (in "pr" mode) opens a Pull Request via the GitHub API.

Auth: uses the token for whichever GitHub account owns that repo (matched by
the "owner" in the repo's origin URL against credentials.json github_accounts).
The token is only ever placed in a throwaway push URL for the `git push`
subprocess call — it's never written to disk or to any file.
"""
import datetime as dt
import logging
import re
import subprocess
from pathlib import Path

import requests

import config
import opencode_bridge

log = logging.getLogger("repo_guardian.git_ops")

REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$")


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60)


def get_origin_info(project_path: Path) -> tuple[str, str] | None:
    """Returns (owner, repo) parsed from the git remote 'origin', or None if not a GitHub repo."""
    r = _run(["git", "remote", "get-url", "origin"], project_path)
    if r.returncode != 0:
        return None
    m = REMOTE_RE.search(r.stdout.strip())
    if not m:
        return None
    return m.group("owner"), m.group("repo")


def has_uncommitted_changes(project_path: Path) -> bool:
    r = _run(["git", "status", "--porcelain"], project_path)
    return r.returncode == 0 and bool(r.stdout.strip())


def default_branch(project_path: Path) -> str:
    r = _run(["git", "symbolic-ref", "--short", "HEAD"], project_path)
    return r.stdout.strip() if r.returncode == 0 else "main"


def _push_url(owner: str, repo: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"


def _diff_summary(project_path: Path) -> str:
    r = _run(["git", "diff", "--cached", "--stat"], project_path)
    return r.stdout.strip()[:1500] if r.returncode == 0 else ""


def commit_and_publish(project_path: Path, changed_paths: list[str]) -> dict:
    """
    Stages everything, commits with an OpenCode-drafted message, and either
    pushes straight to the default branch ("direct" mode) or pushes a new
    branch + opens a PR ("pr" mode, the default).
    Returns a summary dict for notifications.
    """
    summary = {"repo": project_path.name, "committed": False, "pushed": False, "pr_url": None, "branch": None}

    origin = get_origin_info(project_path)
    if not origin:
        log.info("%s has no GitHub 'origin' remote — skipping commit/push", project_path.name)
        return summary
    owner, repo = origin
    token = config.GITHUB_TOKEN_BY_USER.get(owner.lower())
    if not token:
        log.warning("No configured GitHub token for account '%s' (repo %s/%s) — skipping push",
                    owner, owner, repo)
        return summary

    if not has_uncommitted_changes(project_path):
        return summary

    _run(["git", "add", "-A"], project_path)
    diff_summary = _diff_summary(project_path) or f"{len(changed_paths)} file(s) changed"

    message = opencode_bridge.draft_commit_message(str(project_path), diff_summary)
    if not message:
        message = f"chore: update {len(changed_paths)} file(s) via repo-guardian"

    base_branch = default_branch(project_path)

    if config.COMMIT_MODE == "direct":
        r = _run(["git", "commit", "-m", message], project_path)
        summary["committed"] = r.returncode == 0
        if summary["committed"]:
            push = _run(["git", "push", _push_url(owner, repo, token), base_branch], project_path)
            summary["pushed"] = push.returncode == 0
            summary["branch"] = base_branch
        return summary

    # "pr" mode (default): work on a dedicated branch, push it, open a PR
    branch = f"repo-guardian/{dt.date.today().isoformat()}-{project_path.name}"
    _run(["git", "checkout", "-b", branch], project_path)
    r = _run(["git", "commit", "-m", message], project_path)
    summary["committed"] = r.returncode == 0

    if summary["committed"]:
        push = _run(["git", "push", "-u", _push_url(owner, repo, token), branch], project_path)
        summary["pushed"] = push.returncode == 0
        summary["branch"] = branch
        if summary["pushed"]:
            pr = requests.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={
                    "title": message.splitlines()[0][:250],
                    "head": branch,
                    "base": base_branch,
                    "body": f"Automated by repo-guardian from local changes:\n\n"
                            f"```\n{diff_summary}\n```",
                },
                timeout=15,
            )
            if pr.ok:
                summary["pr_url"] = pr.json().get("html_url")
            else:
                log.error("PR creation failed for %s/%s: %s", owner, repo, pr.text[:300])

    # switch back so the user's working copy isn't left on a bot branch
    _run(["git", "checkout", base_branch], project_path)
    return summary
