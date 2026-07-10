"""
Native desktop dashboard for repo-guardian.

Pure Tkinter (Python's built-in GUI toolkit) — no Flask, no HTTP server, no
port, no browser, no WebView2. This is a genuine native OS window; nothing
in its rendering path is web technology of any kind.

Closing the window (the X button) just hides it — repo-guardian keeps
running in the background. Tray icon -> "Open Dashboard" shows it again.
Only tray icon -> "Exit" actually destroys it and quits the app.
"""
import logging
import queue
import threading
import time
import webbrowser
from collections import deque

try:
    import tkinter as tk
    from tkinter import ttk
    _HAS_TK = True
except ImportError:
    _HAS_TK = False

log = logging.getLogger("repo_guardian.dashboard")

_COLORS = {
    "bg": "#0d0f14", "panel": "#161922", "panel2": "#1d212c", "border": "#262b38",
    "text": "#e7e9ee", "muted": "#8891a3", "accent": "#4ade80", "accent_dim": "#1f6f45",
    "err": "#f87171", "warn": "#fbbf24", "info": "#60a5fa",
}

PLATFORMS = [
    ("folder", "Sites folder", "File changes detected on disk"),
    ("github", "GitHub", "Commits, pushes, PRs"),
    ("vercel", "Vercel", "Deployments & status"),
    ("convex", "Convex", "Backend deploys & live errors"),
]

_events = deque(maxlen=300)
_lock = threading.Lock()
_next_id = 0
_ui_queue: "queue.Queue" = queue.Queue()
_start_time = time.time()


def push_event(title: str, message: str, url: str | None = None, level: str = "info",
               category: str = "system") -> dict:
    """Record an event and hand it to the dashboard window (if open) via a
    thread-safe queue. Safe to call from any background thread — folder
    watcher, GitHub/Vercel pollers, Convex log tail, etc. all call this."""
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
            "category": category,
        }
        _events.append(ev)
    _ui_queue.put(ev)
    return ev


def _recent_events() -> list:
    with _lock:
        return list(_events)


class DashboardWindow:
    """The single native dashboard window."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("repo-guardian")
        self.root.geometry("480x780")
        self.root.minsize(420, 560)
        self.root.configure(bg=_COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self.active_filter: str | None = None
        self.all_events: list[dict] = []
        self._platform_widgets: dict[str, dict] = {}
        self._row_urls: dict[str, str] = {}

        self._build_ui()
        self._load_recent()
        self._render_platforms()
        self._render_events()
        self.root.after(300, self._poll_queue)
        self.root.after(200, self._refresh_status)

    # ---------------------------------------------------------------- UI --
    def _build_ui(self):
        root = self.root

        header = tk.Frame(root, bg=_COLORS["bg"])
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(header, text="\u25cf repo-guardian", fg=_COLORS["accent"], bg=_COLORS["bg"],
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        self.mode_pill = tk.Label(header, text="...", fg=_COLORS["muted"], bg=_COLORS["bg"],
                                   font=("Segoe UI", 9))
        self.mode_pill.pack(side="right")

        self.status_frame = tk.Frame(root, bg=_COLORS["bg"])
        self.status_frame.pack(fill="x", padx=16)
        self.status_labels: dict[str, tk.Label] = {}
        for key in ("watch_folder", "poll_interval", "commit_mode", "uptime"):
            row = tk.Frame(self.status_frame, bg=_COLORS["panel"], highlightbackground=_COLORS["border"],
                            highlightthickness=1)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=key.replace("_", " ").upper(), fg=_COLORS["muted"], bg=_COLORS["panel"],
                     font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
            lbl = tk.Label(row, text="—", fg=_COLORS["text"], bg=_COLORS["panel"], font=("Segoe UI", 9),
                           anchor="w", justify="left", wraplength=420)
            lbl.pack(anchor="w", padx=10, pady=(0, 6))
            self.status_labels[key] = lbl

        tk.Label(root, text="PLATFORMS \u2014 CLICK A CARD FOR ITS LIVE ACTIVITY", fg=_COLORS["muted"],
                 bg=_COLORS["bg"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(14, 6))

        grid = tk.Frame(root, bg=_COLORS["bg"])
        grid.pack(fill="x", padx=16)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        for i, (key, name, hint) in enumerate(PLATFORMS):
            card = tk.Frame(grid, bg=_COLORS["panel2"], highlightbackground=_COLORS["border"],
                             highlightthickness=1, cursor="hand2")
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)

            top = tk.Frame(card, bg=_COLORS["panel2"])
            top.pack(fill="x", padx=10, pady=(10, 2))
            dot = tk.Label(top, text="\u25cf", fg=_COLORS["muted"], bg=_COLORS["panel2"], font=("Segoe UI", 9))
            dot.pack(side="left")
            tk.Label(top, text=" " + name, fg=_COLORS["text"], bg=_COLORS["panel2"],
                     font=("Segoe UI", 10, "bold")).pack(side="left")
            count_lbl = tk.Label(top, text="0", fg=_COLORS["muted"], bg=_COLORS["panel2"], font=("Segoe UI", 8))
            count_lbl.pack(side="right")

            last_lbl = tk.Label(card, text=hint, fg=_COLORS["muted"], bg=_COLORS["panel2"], font=("Segoe UI", 9),
                                 anchor="w", justify="left", wraplength=190)
            last_lbl.pack(fill="x", padx=10, pady=(0, 10))

            widgets = {"card": card, "dot": dot, "count": count_lbl, "last": last_lbl, "hint": hint}
            self._platform_widgets[key] = widgets
            for w in (card, top, dot, last_lbl):
                w.bind("<Button-1>", lambda e, k=key: self._toggle_filter(k))

        ev_header = tk.Frame(root, bg=_COLORS["bg"])
        ev_header.pack(fill="x", padx=16, pady=(16, 4))
        self.events_heading = tk.Label(ev_header, text="LIVE ACTIVITY", fg=_COLORS["muted"], bg=_COLORS["bg"],
                                        font=("Segoe UI", 8, "bold"))
        self.events_heading.pack(side="left")
        self.clear_btn = tk.Label(ev_header, text="\u2715 show all", fg=_COLORS["info"], bg=_COLORS["bg"],
                                   font=("Segoe UI", 8), cursor="hand2")
        self.clear_btn.bind("<Button-1>", lambda e: self._toggle_filter(None))

        list_frame = tk.Frame(root, bg=_COLORS["bg"])
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        style.configure("Dash.Treeview", background=_COLORS["panel"], fieldbackground=_COLORS["panel"],
                        foreground=_COLORS["text"], rowheight=44, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Dash.Treeview.Heading", background=_COLORS["panel2"], foreground=_COLORS["muted"],
                        font=("Segoe UI", 8, "bold"))
        style.map("Dash.Treeview", background=[("selected", _COLORS["panel2"])])

        columns = ("time", "title", "message")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", style="Dash.Treeview")
        self.tree.heading("time", text="Time")
        self.tree.heading("title", text="Event")
        self.tree.heading("message", text="Details")
        self.tree.column("time", width=64, anchor="w", stretch=False)
        self.tree.column("title", width=150, anchor="w")
        self.tree.column("message", width=210, anchor="w")
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("success", foreground=_COLORS["accent"])
        self.tree.tag_configure("error", foreground=_COLORS["err"])
        self.tree.tag_configure("info", foreground=_COLORS["info"])
        self.tree.bind("<Double-1>", self._on_row_double_click)

    # ------------------------------------------------------------ events --
    def _load_recent(self):
        self.all_events = list(reversed(_recent_events()))

    def _poll_queue(self):
        drained = False
        while True:
            try:
                ev = _ui_queue.get_nowait()
            except queue.Empty:
                break
            self.all_events.insert(0, ev)
            drained = True
        if drained:
            self.all_events = self.all_events[:300]
            self._render_platforms()
            self._render_events()
        self.root.after(300, self._poll_queue)

    def _toggle_filter(self, key: str | None):
        self.active_filter = None if self.active_filter == key else key
        self._render_platforms()
        self._render_events()

    def _render_platforms(self):
        now = time.time()
        for key, name, hint in PLATFORMS:
            evs = [e for e in self.all_events if e.get("category") == key]
            w = self._platform_widgets[key]
            w["count"].configure(text=str(len(evs)))
            is_active = self.active_filter == key
            border_color = _COLORS["accent"] if is_active else _COLORS["border"]
            w["card"].configure(highlightbackground=border_color)
            if evs:
                last = evs[0]
                try:
                    last_epoch = time.mktime(time.strptime(f"{last['date']} {last['ts']}", "%Y-%m-%d %H:%M:%S"))
                    recent = (now - last_epoch) < 300
                except Exception:  # noqa: BLE001
                    recent = False
                w["dot"].configure(fg=_COLORS["accent"] if recent else _COLORS["muted"])
                w["last"].configure(text=f"{last['title']}  \u00b7  {last['ts']}")
            else:
                w["dot"].configure(fg=_COLORS["muted"])
                w["last"].configure(text=hint)

    def _render_events(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._row_urls.clear()

        if self.active_filter:
            label = next(n for k, n, h in PLATFORMS if k == self.active_filter)
            self.events_heading.configure(text=f"LIVE ACTIVITY \u2014 {label.upper()}")
            self.clear_btn.pack(side="left", padx=(10, 0))
            evs = [e for e in self.all_events if e.get("category") == self.active_filter]
        else:
            self.events_heading.configure(text="LIVE ACTIVITY")
            self.clear_btn.pack_forget()
            evs = self.all_events

        for ev in evs[:150]:
            row_id = self.tree.insert("", "end", values=(ev["ts"], ev["title"], ev["message"].replace("\n", " ")),
                                       tags=(ev.get("level", "info"),))
            if ev.get("url"):
                self._row_urls[row_id] = ev["url"]

    def _on_row_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        url = self._row_urls.get(sel[0])
        if url:
            webbrowser.open(url)

    # ------------------------------------------------------------ status --
    def _refresh_status(self):
        import config  # local import: config may not be loaded until credentials.json exists
        uptime = int(time.time() - _start_time)
        hrs, mins = divmod(uptime // 60, 60)
        self.mode_pill.configure(
            text="LIVE \u2014 applying changes" if config.APPLY_CHANGES else "DRY RUN \u2014 notify only",
            fg=_COLORS["accent"] if config.APPLY_CHANGES else _COLORS["muted"],
        )
        self.status_labels["watch_folder"].configure(text=str(config.WATCH_FOLDER))
        self.status_labels["poll_interval"].configure(text=f"{config.POLL_INTERVAL_MINUTES} min")
        self.status_labels["commit_mode"].configure(text=config.COMMIT_MODE)
        self.status_labels["uptime"].configure(text=f"{hrs}h {mins}m")
        self.root.after(20000, self._refresh_status)

    # ------------------------------------------------------------ window --
    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass


_window: "DashboardWindow | None" = None


def create_window(start_visible: bool = False) -> "DashboardWindow | None":
    """Create the single dashboard window on the CALLING (main) thread.
    Tkinter must live on one thread for the life of the process — call this
    once, from main.py's main thread, before anything else touches Tk."""
    global _window
    if not _HAS_TK:
        log.warning("tkinter not available — no dashboard window will be shown. "
                    "It ships with the standard python.org Windows installer; "
                    "reinstall Python with the default options if this is missing.")
        return None
    _window = DashboardWindow()
    if not start_visible:
        _window.hide()
    return _window


def show() -> None:
    if _window is not None:
        _window.show()


def hide() -> None:
    if _window is not None:
        _window.hide()


def destroy() -> None:
    if _window is not None:
        _window.destroy()


def has_window() -> bool:
    return _window is not None


def mainloop() -> None:
    """Blocking call — run this on the main thread. Returns once the window
    is destroyed (tray -> Exit)."""
    if _window is not None:
        _window.root.mainloop()



