"""
Native Windows 11 toast notifications via win11toast.
Falls back to console printing if win11toast isn't available (e.g. you're
testing this on macOS/Linux) so the rest of the app still runs.
"""
import logging

import dashboard

log = logging.getLogger("repo_guardian.notifier")

try:
    from win11toast import notify as _win_notify
    _HAS_TOAST = True
except ImportError:
    _HAS_TOAST = False
    log.warning("win11toast not available — falling back to console notifications. "
                "Install with: pip install win11toast (Windows only)")


def _infer_level(title: str, message: str) -> str:
    text = f"{title} {message}".lower()
    if "fail" in text or "error" in text or "crash" in text:
        return "error"
    if "deploy" in text or "pushed" in text or "opened" in text or "success" in text:
        return "success"
    return "info"


def notify(title: str, message: str, url: str | None = None, category: str = "system") -> None:
    """
    Show a toast notification and stream the same event to the local
    dashboard (http://127.0.0.1:<port>/). If `url` is given, clicking the
    toast (or the dashboard entry) opens it in the default browser.

    category routes the event to the right live card on the dashboard:
    "folder", "github", "vercel", "convex", or "system".
    """
    log.info("NOTIFY [%s]: %s — %s", category, title, message)
    try:
        dashboard.push_event(title, message, url=url, level=_infer_level(title, message),
                              category=category)
    except Exception as e:  # noqa: BLE001 - dashboard hiccups must never break notifications
        log.error("Dashboard event push failed: %s", e)
    if not _HAS_TOAST:
        print(f"\n[NOTIFICATION] {title}\n{message}\n{'(open: ' + url + ')' if url else ''}\n")
        return

    kwargs = {"duration": "long"}
    if url:
        kwargs["on_click"] = url
    try:
        _win_notify(title, message, **kwargs)
    except Exception as e:  # noqa: BLE001 - never let a notification failure kill the poller
        log.error("Toast notification failed: %s", e)
        print(f"\n[NOTIFICATION] {title}\n{message}\n")
