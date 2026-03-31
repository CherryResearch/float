param(
    [string]$PythonPath = "",
    [switch]$SkipPipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$candidates = @()

if ($PythonPath) {
    $candidates += $PythonPath
}

$candidates += @(
    (Join-Path $repoRoot "backend\venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    "python"
)

$python = $null
foreach ($candidate in $candidates) {
    if ($candidate -eq "python") {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command) {
            $python = $command.Source
            break
        }
        continue
    }
    if (Test-Path $candidate) {
        $python = (Resolve-Path $candidate).Path
        break
    }
}

if (-not $python) {
    throw "Could not locate a Python interpreter. Pass -PythonPath or install the repo venv first."
}

Write-Host "Using Python:" $python

if (-not $SkipPipInstall) {
    & $python -m pip install playwright
    if ($env:OS -eq "Windows_NT") {
        & $python -m pip install pywinauto
    }
}

& $python -m playwright install chromium

Write-Host "Computer-use bootstrap complete."
