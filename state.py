"""
Tiny JSON-backed state store, e.g. {"last_seen_commit": {...}, "last_deploy_status": {...}}
Keeps the tool from spamming you with the same notification every poll cycle.
"""
import json
import threading
from config import STATE_FILE

_lock = threading.Lock()


def load() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save(data: dict) -> None:
    with _lock:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def get(key: str, default=None):
    return load().get(key, default)


def set_key(key: str, value) -> None:
    with _lock:
        data = load()
        data[key] = value
        save(data)
