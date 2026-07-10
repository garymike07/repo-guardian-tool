# repo-guardian v2

A local Windows 11 background tool that watches `C:\Users\ADMIN\OneDrive\Desktop\sites`,
detects changes per project, and automatically commits/deploys them to
GitHub, Vercel, and Convex — with Windows toast notifications at every step.
Uses the [OpenCode](https://opencode.ai) terminal agent (free built-in
models, no API key needed) to draft commit messages and READMEs.

## Safety model (read this first)

- **`credentials.json` never leaves this folder and is gitignored.** It is
  the only place real secret values live. Nothing else — not `AGENTS.md`,
  not any file inside your project folders — ever contains a real token or key.
- **`AGENTS.md`** gets written into each project folder (that's fine to
  commit) but only ever lists variable *names* and the workflow, never values.
- **Two independent safety switches** in `credentials.json`:
  - `apply_changes` (default `false`) — dry run until you flip this. Until
    then you just get "here's what I would do" notifications.
  - `commit_mode`: `"pr"` (default, safe — opens a Pull Request for you to
    review) or `"direct"` (pushes straight to your default branch — only use
    this once you trust the commit messages it's drafting).
- Repo **cleanup only removes known junk** (`.DS_Store`, `__pycache__`,
  `*.log`, `*.bak`, editor backups, etc.) — never source files, never
  anything based on "looks AI-written."

## 1. Install prerequisites (Windows)

- **Python 3.10+**: https://www.python.org/downloads/ (check "Add to PATH")
- **Node.js**: https://nodejs.org (needed for the CLIs below)
- **OpenCode CLI**: `npm i -g opencode-ai@latest`
- **Vercel CLI**: `npm i -g vercel`
- **Convex CLI**: comes via `npx convex` automatically per project, no global install needed

## 2. Install the tool

```powershell
cd C:\path\to\repo_guardian
pip install -r requirements.txt
```

## 3. Configure credentials

```powershell
copy credentials.example.json credentials.json
notepad credentials.json
```

Fill in real values for:
- `github_accounts` — one entry per GitHub account, with a Personal Access
  Token (repo scope) from https://github.com/settings/tokens
- `vercel_accounts` — one entry per Vercel account/email, token from
  https://vercel.com/account/tokens
- `convex_projects` — one entry per Convex project: `name` (label), `folder`
  (the subfolder name under your sites folder), `cloud_url`, and `deploy_key`
  (from the Convex dashboard → Settings → Deploy Keys)
- `watch_folder` is already set to your sites path — change if it differs

**Security note on the credentials you shared in chat:** those tokens now
exist in this conversation's history. Once `credentials.json` is filled in
and working, I'd rotate/regenerate all of them from GitHub, Vercel, and
Convex — each takes under a minute and costs you nothing, and it means the
only live copy of each secret is the one on your machine.

## 4. Link each project once (one-time, per project)

For any project you want Vercel to auto-deploy directly (not just via GitHub
push), run inside that project's folder:

```powershell
cd C:\Users\ADMIN\OneDrive\Desktop\sites\your-project
vercel link
```

For Convex log tailing (optional — deploy still works without this):

```powershell
npx convex login
```

## 5. Run it

```powershell
python main.py
```

A green dot appears in your system tray. From here:
- Save changes anywhere under a project folder → after ~15s of quiet, you get
  a notification of what changed, `AGENTS.md` refreshes, and (if
  `apply_changes=true`) it commits/deploys automatically.
- GitHub commits pushed from elsewhere and Vercel deployment status are
  polled every `poll_interval_minutes` (default 5).
- Convex errors show up the instant they happen (live log tail).

Right-click the tray icon → **Open Dashboard** for a live, in-browser view of
everything repo-guardian is doing (see below). **Exit** stops it.

## 6. The dashboard

A **real native desktop window** (not a browser tab — no address bar, no
browser chrome at all) shows a live feed of every event: file changes
detected, commits/PRs, Convex/Vercel deploys, failures — plus your current
config at a glance (watch folder, dry-run status, linked accounts/projects).
It never displays tokens or deploy keys. It's a normal OS window: drag it
anywhere, resize it, minimize it.

- Open it any time: right-click the tray icon → **Open Dashboard**
- Closing the window (the X button) just hides it — repo-guardian keeps
  running in the background exactly as before. Use tray → **Exit** to
  actually quit.
- Set `"show_dashboard_on_start": true` in `credentials.json` if you want it
  to pop open automatically every time repo-guardian starts.
- Under the hood it's powered by Windows' built-in WebView2 runtime (already
  installed on Windows 10/11 — same engine as Edge, but with no browser UI
  around it) via the `pywebview` package. If `pywebview` isn't installed, it
  falls back automatically to opening your regular browser at
  http://127.0.0.1:47591/ (port configurable via `dashboard_port`).

## 7. Run automatically at Windows login, and auto-restart if it ever crashes

Two pieces, both already in this repo:

- **`run_repo_guardian.bat`** — a supervisor loop. It launches `main.py`
  (via `pythonw.exe`, so no console window) and, if it ever exits for any
  reason, waits 10s and starts it again — forever.
- **`setup_autostart.ps1`** — registers a hidden launcher in your Startup
  folder so `run_repo_guardian.bat` starts automatically, invisibly, every
  time you log in to Windows.

One-time setup:

```powershell
cd C:\repo-guardian
powershell -ExecutionPolicy Bypass -File .\setup_autostart.ps1
```

To start it immediately without logging out/in or restarting:

```powershell
wscript.exe C:\repo-guardian\launch_hidden.vbs
```

To confirm it's alive, right-click the tray icon (it appears within a couple
seconds) or open the dashboard at http://127.0.0.1:47591/. To watch the
supervisor's restart history: `type C:\repo-guardian\logs\supervisor.log`

Only one copy of repo-guardian ever runs at a time — if you manually run
`python main.py` while the autostarted copy is already running, it detects
that and exits immediately rather than double-deploying anything.

## What each file does

| File | Purpose |
|---|---|
| `main.py` | Orchestrates everything |
| `folder_watcher.py` | Watches your sites folder, debounces changes per project |
| `git_ops.py` | Local commit/push/PR from a changed project folder |
| `deploy.py` | Real `vercel --prod` / `npx convex deploy` triggers |
| `github_manager.py` | Multi-account GitHub API: repo discovery, remote cleanup/README pass |
| `vercel_monitor.py` | Multi-account Vercel deployment status polling |
| `convex_monitor.py` | Live Convex log tailing per project |
| `agents_md.py` | Writes AGENTS.md per project (workflow + env var names, no secrets) |
| `env_scanner.py` | Scans project source for referenced env var names |
| `opencode_bridge.py` | Calls the OpenCode CLI for commit messages / README drafts |
| `notifier.py` | Windows 11 toast notifications |
| `dashboard.py` | Local live-event web dashboard (Flask + Server-Sent Events) |
| `run_repo_guardian.bat` | Supervisor loop — restarts `main.py` forever if it crashes |
| `launch_hidden.vbs` | Launches the supervisor with zero visible windows |
| `setup_autostart.ps1` | One-time: registers hidden auto-start at Windows login |

## Limitations

- This is a "while my laptop is on and this script is running" tool, not a
  24/7 cloud service — if the machine sleeps, nothing is watched.
- Vercel/GitHub status is polled (not instant webhooks), since this runs
  without a public URL. Convex errors are the exception — those are live.
- `vercel --prod` deploys straight from your local folder; if the same
  project is also Git-linked in Vercel's dashboard, a GitHub push triggers
  its own separate deploy too — both succeeding is normal, not a conflict.
