' Runs run_repo_guardian.bat completely hidden (no console window at all).
' Used by the Startup shortcut created by setup_autostart.ps1.
Set objShell = CreateObject("WScript.Shell")
objShell.Run """C:\repo-guardian\run_repo_guardian.bat""", 0, False
