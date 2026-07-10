"""
GitHub side: multi-account repo discovery + a periodic remote cleanup/README
pass via the API (separate from git_ops.py, which handles LOCAL commits
triggered by the folder watcher).

Safety model unchanged from before:
- Cleanup only removes known junk patterns (OS cruft, caches, logs, backups)
  — never guesses at "AI-looking" code.
- Changes go out as a PR on a repo-guardian/<date> branch, never a direct
  push to the default branch, unless commit_mode == "direct".
"""
import base64
import datetime as dt
import fnmatch
import logging

import requests

import config
import opencode_bridge

log = logging.getLogger("repo_guardian.github")
API = "https://api.github.com"

JUNK_PATTERNS = [
    ".DS_Store", "Thumbs.db", "desktop.ini",
    "*.pyc", "__pycache__/*", "*.pyo",
    "*.log", "*.tmp", "*.temp", "*.bak", "*~",
    ".pytest_cache/*", ".mypy_cache/*",
    "*.orig", "*.rej",
    "npm-debug.log*", "yarn-error.log*",
]
PROTECTED = {".git", ".github", "LICENSE", "LICENSE.md"}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def list_all_repos() -> list[dict]:
    """Aggregates repos across every configured GitHub account. Each repo dict gets a '_token' key added."""
    all_repos = []
    for account in config.GITHUB_ACCOUNTS:
        token, username = account["token"], account["username"]
        page = 1
        while True:
            r = requests.get(
                f"{API}/users/{username}/repos",
                headers=_headers(token), params={"per_page": 100, "page": page, "type": "owner"},
                timeout=15,
            )
            if not r.ok:
                log.error("Could not list repos for %s: %s", username, r.text[:200])
                break
            batch = r.json()
            if not batch:
                break
            for repo in batch:
                repo["_token"] = token
                repo["_account_label"] = account["label"]
            all_repos.extend(batch)
            page += 1
    return all_repos


def get_latest_commit_sha(repo_full_name: str, branch: str, token: str) -> str | None:
    r = requests.get(f"{API}/repos/{repo_full_name}/commits/{branch}", headers=_headers(token), timeout=15)
    return r.json().get("sha") if r.ok else None


def get_repo_tree(repo_full_name: str, branch: str, token: str) -> list[dict]:
    r = requests.get(
        f"{API}/repos/{repo_full_name}/git/trees/{branch}",
        headers=_headers(token), params={"recursive": "1"}, timeout=20,
    )
    r.raise_for_status()
    return [item for item in r.json().get("tree", []) if item["type"] == "blob"]


def find_junk_files(files: list[dict]) -> list[str]:
    junk = []
    for f in files:
        path = f["path"]
        if path.split("/")[0] in PROTECTED:
            continue
        if any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path.split("/")[-1], pat) for pat in JUNK_PATTERNS):
            junk.append(path)
    return junk


def _create_branch(repo_full_name: str, base_branch: str, new_branch: str, token: str) -> bool:
    base_sha = get_latest_commit_sha(repo_full_name, base_branch, token)
    if not base_sha:
        return False
    r = requests.post(
        f"{API}/repos/{repo_full_name}/git/refs", headers=_headers(token),
        json={"ref": f"refs/heads/{new_branch}", "sha": base_sha}, timeout=15,
    )
    return r.ok or "Reference already exists" in r.text


def _delete_file(repo_full_name: str, path: str, branch: str, sha: str, message: str, token: str) -> bool:
    r = requests.delete(
        f"{API}/repos/{repo_full_name}/contents/{path}", headers=_headers(token),
        json={"message": message, "sha": sha, "branch": branch}, timeout=15,
    )
    return r.ok


def _get_file_sha(repo_full_name: str, path: str, branch: str, token: str) -> str | None:
    r = requests.get(f"{API}/repos/{repo_full_name}/contents/{path}", headers=_headers(token),
                      params={"ref": branch}, timeout=15)
    return r.json().get("sha") if r.ok else None


def _put_file(repo_full_name: str, path: str, branch: str, content_str: str, message: str,
               token: str, existing_sha: str | None = None) -> bool:
    payload = {"message": message, "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
               "branch": branch}
    if existing_sha:
        payload["sha"] = existing_sha
    r = requests.put(f"{API}/repos/{repo_full_name}/contents/{path}", headers=_headers(token),
                      json=payload, timeout=15)
    return r.ok


def _open_pr(repo_full_name: str, branch: str, base_branch: str, title: str, body: str, token: str) -> str | None:
    r = requests.post(f"{API}/repos/{repo_full_name}/pulls", headers=_headers(token),
                       json={"title": title, "head": branch, "base": base_branch, "body": body}, timeout=15)
    if r.ok:
        return r.json().get("html_url")
    log.error("PR creation failed for %s: %s", repo_full_name, r.text[:300])
    return None


def _fallback_readme(repo: dict, languages: list[str]) -> str:
    lines = [f"# {repo['name']}", "", repo.get("description") or "No description provided yet.", ""]
    if languages:
        lines += ["## Tech Stack", "", ", ".join(f"`{l}`" for l in languages), ""]
    lines += ["## Getting Started", "", "```bash", f"git clone {repo['clone_url']}",
              f"cd {repo['name']}", "```", ""]
    return "\n".join(lines)


def build_readme(repo: dict, local_clone_hint: str | None = None) -> str:
    """Tries OpenCode first (if a local clone path is known and opencode is installed), else a template."""
    r = requests.get(repo["languages_url"], headers=_headers(repo["_token"]), timeout=15)
    languages = list(r.json().keys()) if r.ok else []

    if local_clone_hint and opencode_bridge.is_available():
        drafted = opencode_bridge.draft_readme(local_clone_hint, repo["name"])
        if drafted:
            return drafted
    return _fallback_readme(repo, languages)


def remote_cleanup_pass(repo: dict) -> dict:
    """Remote (API-based) cleanup + README pass — for repos not necessarily cloned locally in the watch folder."""
    full_name, token = repo["full_name"], repo["_token"]
    base_branch = repo.get("default_branch", "main")
    summary = {"repo": full_name, "junk_files": [], "readme_updated": False, "pr_url": None}

    files = get_repo_tree(full_name, base_branch, token)
    junk = find_junk_files(files)
    summary["junk_files"] = junk
    readme_needed = not any(f["path"].lower() == "readme.md" for f in files)

    if not junk and not readme_needed:
        return summary
    if not config.APPLY_CHANGES:
        log.info("[DRY RUN] %s: would remove %d junk file(s), readme_needed=%s",
                  full_name, len(junk), readme_needed)
        return summary

    branch = f"repo-guardian/{dt.date.today().isoformat()}"
    if not _create_branch(full_name, base_branch, branch, token):
        return summary

    for path in junk:
        sha = _get_file_sha(full_name, path, branch, token)
        if sha:
            _delete_file(full_name, path, branch, sha, f"chore: remove {path} (repo-guardian)", token)

    if readme_needed:
        content = build_readme(repo)
        _put_file(full_name, "README.md", branch, content, "docs: add README (repo-guardian)", token)
        summary["readme_updated"] = True

    body = ["Automated maintenance by repo-guardian:", ""]
    if junk:
        body.append(f"- Removed {len(junk)} clutter file(s)")
    if readme_needed:
        body.append("- Added a README")
    pr_url = _open_pr(full_name, branch, base_branch, "repo-guardian: cleanup & docs", "\n".join(body), token)
    summary["pr_url"] = pr_url
    return summary
