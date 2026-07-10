"""
Native Windows 11 toast notifications via win11toast.
Falls back to console printing if win11toast isn't available (e.g. you're
testing this on macOS/Linux) so the rest of the app still runs.
"""
import logging

log = logging.getLogger("repo_guardian.notifier")

try:
    from win11toast import notify as _win_notify
    _HAS_TOAST = True
except ImportError:
    _HAS_TOAST = False
    log.warning("win11toast not available — falling back to console notifications. "
                "Install with: pip install win11toast (Windows only)")


def notify(title: str, message: str, url: str | None = None) -> None:
    """
    Show a toast notification. If `url` is given, clicking the toast opens it
    in the default browser (e.g. link straight to the failed Vercel deployment
    or the GitHub PR).
    """
    log.info("NOTIFY: %s — %s", title, message)
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
