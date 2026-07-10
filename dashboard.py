"""
Local-only web dashboard for repo-guardian.

Runs a small Flask server on 127.0.0.1 (never exposed to the network) that
shows a live feed of everything repo-guardian is doing, plus a snapshot of
what's configured (accounts, projects, watch folder, dry-run status).

Nothing here ever serves tokens/deploy keys — only labels/usernames/emails.

Opened via the tray icon ("Open Dashboard"), or manually at
http://127.0.0.1:<port>/ in any browser.
"""
import json
import logging
import queue
import threading
import time
from collections import deque

from flask import Flask, Response, jsonify, render_template_string

log = logging.getLogger("repo_guardian.dashboard")

app = Flask(__name__)
_start_time = time.time()

_events = deque(maxlen=300)
_subscribers: list[queue.Queue] = []
_lock = threading.Lock()
_next_id = 0


def push_event(title: str, message: str, url: str | None = None, level: str = "info") -> dict:
    """Record an event and fan it out to any open dashboard tabs."""
    global _next_id
    with _lock:
        _next_id += 1
        ev = {
            "id": _next_id,
            "ts": time.strftime("%H:%M:%S"),
            "date": time.strftime("%Y-%m-%d"),
            "title": title,
            "message": message,
            "url": url,
            "level": level,
        }
        _events.append(ev)
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(ev)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)
    return ev


def _recent_events() -> list:
    with _lock:
        return list(_events)


def _subscribe() -> "queue.Queue":
    q: "queue.Queue" = queue.Queue(maxsize=100)
    with _lock:
        _subscribers.append(q)
    return q


def _unsubscribe(q: "queue.Queue") -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


@app.route("/")
def index():
    return render_template_string(_PAGE_HTML)


@app.route("/api/status")
def api_status():
    import config  # local import: config may not be loaded until after credentials.json exists

    uptime = int(time.time() - _start_time)
    return jsonify({
        "watch_folder": str(config.WATCH_FOLDER),
        "apply_changes": config.APPLY_CHANGES,
        "commit_mode": config.COMMIT_MODE,
        "poll_interval_minutes": config.POLL_INTERVAL_MINUTES,
        "github_accounts": [a.get("label") or a.get("username") for a in config.GITHUB_ACCOUNTS],
        "vercel_accounts": [a.get("label") or a.get("email") for a in config.VERCEL_ACCOUNTS],
        "convex_projects": [p.get("name") for p in config.CONVEX_PROJECTS],
        "uptime_seconds": uptime,
    })


@app.route("/api/events")
def api_events():
    return jsonify(_recent_events())


@app.route("/api/stream")
def api_stream():
    def gen():
        q = _subscribe()
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            _unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def run(host: str = "127.0.0.1", port: int = 47591) -> None:
    """Blocking call — run this in a daemon thread from main.py."""
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    try:
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
    except Exception as e:  # noqa: BLE001
        log.error("Dashboard server failed to start on %s:%s — %s", host, port, e)


_PAGE_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>repo-guardian</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0d0f14; --panel: #161922; --panel-2: #1d212c; --border: #262b38;
    --text: #e7e9ee; --muted: #8891a3; --accent: #4ade80; --accent-dim: #1f6f45;
    --err: #f87171; --warn: #fbbf24; --info: #60a5fa;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    min-height: 100vh;
  }
  header {
    padding: 18px 22px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; background: var(--bg); z-index: 5;
  }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 17px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent);
         box-shadow: 0 0 0 0 rgba(74,222,128,.6); animation: pulse 1.8s infinite; }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(74,222,128,.55); }
    70% { box-shadow: 0 0 0 8px rgba(74,222,128,0); }
    100% { box-shadow: 0 0 0 0 rgba(74,222,128,0); }
  }
  .pill { font-size: 12px; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border);
          color: var(--muted); }
  .pill.on { color: var(--accent); border-color: var(--accent-dim); }
  main { padding: 20px 22px 60px; max-width: 900px; margin: 0 auto; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 22px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .card .label { color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
  .card .value { font-size: 14px; word-break: break-word; }
  .card .value.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; color: var(--muted); }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin: 0 0 12px; }
  #events { display: flex; flex-direction: column; gap: 8px; }
  .ev { background: var(--panel); border: 1px solid var(--border); border-left: 3px solid var(--info);
        border-radius: 8px; padding: 10px 14px; animation: slidein .25s ease; }
  .ev.success { border-left-color: var(--accent); }
  .ev.error { border-left-color: var(--err); }
  @keyframes slidein { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }
  .ev-top { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
  .ev-title { font-weight: 600; font-size: 13.5px; }
  .ev-time { color: var(--muted); font-size: 11.5px; font-family: ui-monospace, Consolas, monospace; white-space: nowrap; }
  .ev-msg { color: #c3c8d4; font-size: 12.5px; margin-top: 4px; white-space: pre-wrap; }
  .ev a { color: var(--info); text-decoration: none; }
  .empty { color: var(--muted); font-size: 13px; padding: 30px 0; text-align: center; }
</style>
</head>
<body>
<header>
  <div class="brand"><span class="dot"></span> repo-guardian</div>
  <span class="pill" id="mode-pill">…</span>
</header>
<main>
  <div class="grid" id="status-grid"></div>
  <h2>Live activity</h2>
  <div id="events"><div class="empty">Waiting for the first event…</div></div>
</main>
<script>
async function loadStatus() {
  const r = await fetch('/api/status'); const s = await r.json();
  const pill = document.getElementById('mode-pill');
  pill.textContent = s.apply_changes ? 'LIVE — applying changes' : 'DRY RUN — notify only';
  pill.className = 'pill' + (s.apply_changes ? ' on' : '');
  const hrs = Math.floor(s.uptime_seconds/3600), mins = Math.floor((s.uptime_seconds%3600)/60);
  document.getElementById('status-grid').innerHTML = `
    <div class="card"><div class="label">Watch folder</div><div class="value mono">${s.watch_folder}</div></div>
    <div class="card"><div class="label">Poll interval</div><div class="value">${s.poll_interval_minutes} min</div></div>
    <div class="card"><div class="label">Commit mode</div><div class="value">${s.commit_mode}</div></div>
    <div class="card"><div class="label">Uptime</div><div class="value">${hrs}h ${mins}m</div></div>
    <div class="card"><div class="label">GitHub accounts</div><div class="value">${s.github_accounts.join(', ') || '—'}</div></div>
    <div class="card"><div class="label">Vercel accounts</div><div class="value">${s.vercel_accounts.join(', ') || '—'}</div></div>
    <div class="card"><div class="label">Convex projects</div><div class="value">${s.convex_projects.join(', ') || '—'}</div></div>
  `;
}
function renderEvent(ev) {
  const box = document.getElementById('events');
  if (box.querySelector('.empty')) box.innerHTML = '';
  const div = document.createElement('div');
  div.className = 'ev ' + (ev.level || 'info');
  const msg = ev.url ? `${ev.message}<br><a href="${ev.url}" target="_blank">Open →</a>` : ev.message;
  div.innerHTML = `<div class="ev-top"><span class="ev-title">${ev.title}</span><span class="ev-time">${ev.ts}</span></div><div class="ev-msg">${msg}</div>`;
  box.prepend(div);
  while (box.children.length > 150) box.removeChild(box.lastChild);
}
async function loadRecent() {
  const r = await fetch('/api/events'); const evs = await r.json();
  evs.slice().reverse().forEach(renderEvent);
}
loadStatus(); loadRecent();
setInterval(loadStatus, 20000);
const es = new EventSource('/api/stream');
es.onmessage = (e) => renderEvent(JSON.parse(e.data));
</script>
</body>
</html>
"""
