"""
Actually triggers deploys — this is the "way faster, without going to the
platforms" piece. Both only run when config.APPLY_CHANGES is true; otherwise
they're skipped and the caller just notifies "would deploy".

Vercel: uses the Vercel CLI (`npm i -g vercel`) with a token, deploying the
local folder directly — this does NOT require the project to be pushed to
GitHub first (a proper standalone deploy), which is faster for iteration.
Note: if the project is ALSO connected to GitHub via Vercel's Git
integration, pushing to GitHub triggers Vercel's own separate auto-deploy —
both can run; that's normal and not a conflict.

Convex: uses the Convex CLI (`npm i -g convex`, or the project's local
`npx convex`) with CONVEX_DEPLOY_KEY set from credentials.json for that
specific project — this deploys the backend functions/schema in convex/.
"""
import logging
import os
import re
import subprocess
from pathlib import Path

import config

log = logging.getLogger("repo_guardian.deploy")


def _run(cmd: list[str], cwd: Path, extra_env: dict | None = None, timeout: int = 300):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)


def deploy_vercel(project_path: Path, vercel_token: str) -> dict:
    """Runs `vercel --prod --yes --token <token>` in the project folder."""
    result = {"success": False, "url": None, "output": ""}
    r = _run(["vercel", "--prod", "--yes", "--token", vercel_token], project_path, timeout=600)
    result["output"] = (r.stdout + r.stderr)[-2000:]
    result["success"] = r.returncode == 0
    if result["success"]:
        m = re.search(r"https://\S+\.vercel\.app", r.stdout)
        if m:
            result["url"] = m.group(0)
    else:
        log.error("Vercel deploy failed for %s: %s", project_path.name, result["output"][-500:])
    return result


def deploy_convex(project_path: Path, deploy_key: str) -> dict:
    """Runs `npx convex deploy` in the project folder with CONVEX_DEPLOY_KEY set."""
    result = {"success": False, "output": ""}
    r = _run(["npx", "convex", "deploy", "--yes"], project_path,
              extra_env={"CONVEX_DEPLOY_KEY": deploy_key}, timeout=300)
    result["output"] = (r.stdout + r.stderr)[-2000:]
    result["success"] = r.returncode == 0
    if not result["success"]:
        log.error("Convex deploy failed for %s: %s", project_path.name, result["output"][-500:])
    return result


def has_convex_changes(changed_paths: list[str]) -> bool:
    return any("convex" in Path(p).parts for p in changed_paths)
