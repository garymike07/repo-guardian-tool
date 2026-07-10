"""
Polls the Vercel REST API across all configured accounts for deployment
status changes. Local, no public endpoint needed.
"""
import logging

import requests

import config
import state

log = logging.getLogger("repo_guardian.vercel")
API = "https://api.vercel.com"


def list_projects(token: str) -> list[dict]:
    r = requests.get(f"{API}/v9/projects", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    return r.json().get("projects", []) if r.ok else []


def _latest_deployment(token: str, project_id: str) -> dict | None:
    r = requests.get(f"{API}/v6/deployments", headers={"Authorization": f"Bearer {token}"},
                      params={"projectId": project_id, "limit": 1}, timeout=15)
    if not r.ok:
        return None
    deployments = r.json().get("deployments", [])
    return deployments[0] if deployments else None


def check_all_accounts() -> list[dict]:
    events = []
    seen = state.get("vercel_last_status", {})

    for account in config.VERCEL_ACCOUNTS:
        token, label = account["token"], account["label"]
        for project in list_projects(token):
            pid, pname = project["id"], project["name"]
            key = f"{label}:{pid}"
            dep = _latest_deployment(token, pid)
            if not dep:
                continue
            status = dep.get("readyState") or dep.get("state")
            if status != seen.get(key):
                events.append({
                    "account_label": label,
                    "project_name": pname,
                    "status": status,
                    "url": f"https://{dep.get('url')}" if dep.get("url") else None,
                    "inspector_url": dep.get("inspectorUrl"),
                })
                seen[key] = status

    state.set_key("vercel_last_status", seen)
    return events
