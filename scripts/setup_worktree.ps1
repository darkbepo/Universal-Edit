# Prepare a git worktree for work: its own Python venv (editable install of THIS worktree's
# code), the website's node_modules, and a copy of the bundled binaries from the main checkout.
#   powershell -File scripts\setup_worktree.ps1 -Worktree .claude\worktrees\<name>
param([Parameter(Mandatory = $true)][string]$Worktree)
$ErrorActionPreference = "Stop"
$main = (git -C $Worktree rev-parse --path-format=absolute --git-common-dir) -replace "[\\/]\.git$", ""
$wt = (Resolve-Path $Worktree).Path
Write-Host "Worktree: $wt   (main checkout: $main)"

# Python: a venv per worktree — a shared one would run the main checkout's code.
if (-not (Test-Path "$wt\.venv")) { py -3.12 -m venv "$wt\.venv" }
& "$wt\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$wt\.venv\Scripts\python.exe" -m pip install -q -e "$wt[dev]"

# Website packages.
Push-Location "$wt\web"; try { npm ci --no-audit --no-fund | Out-Null } finally { Pop-Location }

# Git-ignored binaries (FFmpeg, OCR data, fonts) are copied rather than downloaded again.
foreach ($d in "packaging\bin", "packaging\tessdata", "packaging\fonts") {
  if ((Test-Path "$main\$d") -and -not (Test-Path "$wt\$d")) { Copy-Item -Recurse "$main\$d" "$wt\$d" }
}
Write-Host "Ready: $wt"
