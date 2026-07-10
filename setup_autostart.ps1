# Registers repo-guardian to start automatically, hidden, at every logon.
# Safe to re-run any time (it just overwrites the same shortcut).
# Run this once from: C:\repo-guardian>  powershell -ExecutionPolicy Bypass -File .\setup_autostart.ps1

$repoDir   = "C:\repo-guardian"
$vbsPath   = Join-Path $repoDir "launch_hidden.vbs"
$startup   = [Environment]::GetFolderPath('Startup')
$shortcut  = Join-Path $startup "repo-guardian.lnk"

if (-not (Test-Path $vbsPath)) {
    Write-Host "ERROR: $vbsPath not found. Run this from inside C:\repo-guardian after 'git pull'." -ForegroundColor Red
    exit 1
}

$WshShell = New-Object -ComObject WScript.Shell
$sc = $WshShell.CreateShortcut($shortcut)
$sc.TargetPath       = "$env:WINDIR\System32\wscript.exe"
$sc.Arguments        = "`"$vbsPath`""
$sc.WorkingDirectory = $repoDir
$sc.Description      = "repo-guardian - hidden auto-start, auto-restarting"
$sc.Save()

Write-Host "Done. Startup shortcut created at:" -ForegroundColor Green
Write-Host "  $shortcut"
Write-Host ""
Write-Host "repo-guardian will now launch automatically and silently every time you log in to Windows,"
Write-Host "and will keep restarting itself in the background if it ever crashes."
Write-Host ""
Write-Host "To start it right now without restarting your laptop, run:"
Write-Host "  wscript.exe `"$vbsPath`""
Write-Host ""
Write-Host "To remove auto-start later, just delete:"
Write-Host "  $shortcut"
