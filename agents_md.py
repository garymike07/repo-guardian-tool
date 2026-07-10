"""
Writes AGENTS.md into a project folder so any coding agent working in that
repo (you, OpenCode, Claude Code, whatever) knows the deploy workflow and
which environment variables the project needs.

Hard rule: this file NEVER contains a real secret value. It lists variable
NAMES and says where to get the value (your local credentials.json vault),
because AGENTS.md lives inside a folder that gets git-committed and pushed —
a real key in here would end up on GitHub the moment you push.
"""
from pathlib import Path

import config
import env_scanner


def _vercel_account_labels() -> str:
    return ", ".join(a["label"] for a in config.VERCEL_ACCOUNTS) or "(none configured)"


def build(project_path: Path, project_name: str, github_full_name: str | None) -> str:
    env_vars = sorted(env_scanner.scan(project_path))
    convex_project = config.convex_project_by_folder(project_name)

    lines = [
        f"# AGENTS.md — {project_name}",
        "",
        "This file is generated and refreshed automatically by repo-guardian.",
        "It tells any AI coding agent (or you) how changes in this folder get",
        "committed and deployed. It intentionally contains NO real secret values —",
        "only variable names. Real values live in a local, gitignored",
        "`credentials.json` on this machine, outside any git repo.",
        "",
        "## Deploy targets for this project",
        "",
    ]

    if github_full_name:
        lines += [f"- **GitHub repo:** `{github_full_name}`"]
    if convex_project:
        lines += [f"- **Convex deployment:** `{convex_project['cloud_url']}`"]
    lines += [f"- **Vercel account(s) available:** {_vercel_account_labels()}", ""]

    lines += [
        "## Environment variables this project references",
        "",
    ]
    if env_vars:
        lines += [f"- `{v}`" for v in env_vars]
    else:
        lines += ["(none detected by static scan — check manually if this project uses secrets)"]

    lines += [
        "",
        "Real values for the above are stored in `credentials.json` on the developer's",
        "machine (not in this repo). Do not hardcode secret values into source files",
        "or commit a `.env` file with real values — commit only `.env.example` with",
        "variable names and placeholder values.",
        "",
        "## Commit & deploy procedure",
        "",
        f"1. Make your changes normally in this folder.",
        f"2. repo-guardian detects the change automatically (or run it manually) and:",
    ]

    if config.COMMIT_MODE == "direct":
        lines += [
            "   - Stages, commits (AI-drafted commit message via OpenCode), and pushes "
            "directly to the default branch.",
        ]
    else:
        lines += [
            "   - Stages and commits on a `repo-guardian/<date>` branch (AI-drafted commit "
            "message via OpenCode) and opens a Pull Request — review and merge it yourself.",
        ]

    lines += [
        "3. Pushing to GitHub triggers Vercel's own auto-deploy (if this project is linked "
        "to Vercel via Git integration) — no manual action needed on vercel.com.",
    ]
    if convex_project:
        lines += [
            "4. If files under `convex/` changed, repo-guardian runs "
            "`npx convex deploy` using this project's deploy key automatically "
            "(when apply_changes is enabled in credentials.json).",
        ]
    lines += [
        "",
        "## Manual commands (if you want to do a step yourself)",
        "",
        "```bash",
        "git add -A",
        'git commit -m "your message"',
        "git push",
        "```",
    ]
    if convex_project:
        lines += [
            "",
            "```bash",
            "# Deploy Convex backend for this project (from within this folder):",
            "# set CONVEX_DEPLOY_KEY from credentials.json first, then:",
            "npx convex deploy",
            "```",
        ]

    lines.append("")
    return "\n".join(lines)


def write(project_path: Path, project_name: str, github_full_name: str | None) -> Path:
    content = build(project_path, project_name, github_full_name)
    out_path = project_path / "AGENTS.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path
