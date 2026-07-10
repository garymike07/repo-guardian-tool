"""
Thin wrapper around the `opencode` terminal agent (https://opencode.ai).

Used only for the parts that genuinely benefit from a model looking at real
content — writing a commit message from an actual diff, drafting README
prose from actual repo structure. It is NOT used to decide what files to
delete; that stays deterministic (see github_manager.find_junk_files) so
cleanup behavior is predictable and auditable.

Requires the `opencode` CLI installed and on PATH:
    npm i -g opencode-ai@latest
No API key needed for the built-in free models opencode ships with.
"""
import logging
import shutil
import subprocess

import config

log = logging.getLogger("repo_guardian.opencode")

_AVAILABLE = shutil.which("opencode") is not None
if not _AVAILABLE:
    log.warning("opencode CLI not found on PATH. Install with: npm i -g opencode-ai@latest. "
                "Falling back to plain-text templates for commit messages / README drafts.")


def is_available() -> bool:
    return _AVAILABLE


def run_prompt(prompt: str, cwd: str, timeout: int = 90) -> str | None:
    """
    Runs `opencode run "<prompt>" -q` (non-interactive, quiet) inside `cwd`
    and returns the model's text output, or None on failure/unavailable.
    """
    if not _AVAILABLE:
        return None

    cmd = ["opencode", "run", prompt, "-q"]
    if config.OPENCODE_MODEL:
        cmd += ["-m", config.OPENCODE_MODEL]

    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.error("opencode call timed out after %ss", timeout)
        return None
    except Exception as e:  # noqa: BLE001
        log.error("opencode call failed: %s", e)
        return None

    if result.returncode != 0:
        log.error("opencode exited %s: %s", result.returncode, result.stderr[:300])
        return None
    return result.stdout.strip()


def draft_commit_message(cwd: str, diff_summary: str) -> str | None:
    prompt = (
        "Based on the currently staged git changes in this repository, write ONE concise "
        "conventional-commit style message (type: short summary, <=72 chars on the first line). "
        "Output ONLY the commit message, nothing else. Context: " + diff_summary
    )
    return run_prompt(prompt, cwd)


def draft_readme(cwd: str, project_name: str) -> str | None:
    prompt = (
        f"Look at this repository's actual source files and write a professional README.md for "
        f"the project '{project_name}': what it does, tech stack, setup/run instructions. "
        f"Base it only on what you find in the code — don't invent features. "
        f"Output ONLY the raw markdown content, nothing else."
    )
    return run_prompt(prompt, cwd, timeout=150)
