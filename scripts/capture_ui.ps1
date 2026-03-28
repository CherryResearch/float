param(
  [string]$OutputPath = "data/screenshots/ui.png",
  [string]$Route = "/?tab=threads",
  [int]$Width = 1440,
  [int]$Height = 900,
  [int]$VirtualTimeMs = 20000,
  [int]$TimeoutSec = 90,
  [int]$FrontendPort = 0,
  [int]$BackendPort = 0
)

$ErrorActionPreference = "Stop"

function Wait-HttpReady {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][int]$WaitSeconds
  )
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        return
      }
    } catch {
      # Keep polling until timeout.
    }
    Start-Sleep -Milliseconds 600
  }
  throw "Timed out waiting for HTTP readiness: $Url"
}

function Test-ApiProviderReady {
  param(
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][int]$WaitSeconds
  )
  try {
    $settings = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/settings" -UseBasicParsing -TimeoutSec 6
    $settingsJson = $settings.Content | ConvertFrom-Json
    $mode = [string]$settingsJson.mode
    if ($mode -ne "api") {
      return
    }
  } catch {
    # If settings cannot be read, skip provider-specific checks.
    return
  }

  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $provider = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/openai/models" -UseBasicParsing -TimeoutSec 8
      if ($provider.StatusCode -ge 200 -and $provider.StatusCode -lt 500) {
        return
      }
    } catch {
      # Keep polling until timeout to avoid capturing "API offline" placeholders.
    }
    Start-Sleep -Milliseconds 700
  }
  throw "API mode detected, but /api/openai/models did not become ready before timeout."
}

function Resolve-BrowserPath {
  $fromPath = @(
    (Get-Command chrome -ErrorAction SilentlyContinue).Source,
    (Get-Command msedge -ErrorAction SilentlyContinue).Source
  ) | Where-Object { $_ }
  if ($fromPath.Count -gt 0) {
    return $fromPath[0]
  }

  $fallback = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
  )
  foreach ($path in $fallback) {
    if ($path -and (Test-Path $path)) {
      return $path
    }
  }
  throw "No Chromium browser found. Install Chrome/Edge or add it to PATH."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
  if ((($FrontendPort -le 0) -or ($BackendPort -le 0)) -and (Test-Path ".dev_state.json")) {
    try {
      $state = Get-Content ".dev_state.json" -Raw | ConvertFrom-Json
      if ($FrontendPort -le 0 -and $state.frontend_port) {
        $FrontendPort = [int]$state.frontend_port
      }
      if ($BackendPort -le 0 -and $state.backend_port) {
        $BackendPort = [int]$state.backend_port
      }
    } catch {
      # Fallback to explicit params if .dev_state.json cannot be parsed.
    }
  }

  if ($FrontendPort -le 0) {
    throw "Frontend port not provided and could not be read from .dev_state.json."
  }

  if ($BackendPort -gt 0) {
    Wait-HttpReady -Url "http://127.0.0.1:$BackendPort/health" -WaitSeconds $TimeoutSec
    Test-ApiProviderReady -Port $BackendPort -WaitSeconds $TimeoutSec
  }
  Wait-HttpReady -Url "http://localhost:$FrontendPort/" -WaitSeconds $TimeoutSec

  $browser = Resolve-BrowserPath
  $normalizedRoute = if ([string]::IsNullOrWhiteSpace($Route)) {
    "/"
  } elseif ($Route.StartsWith("/")) {
    $Route
  } else {
    "/$Route"
  }
  $targetUrl = "http://localhost:$FrontendPort$normalizedRoute"

  $resolvedOutput = if ([IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath
  } else {
    Join-Path $repoRoot $OutputPath
  }
  $outputDir = Split-Path -Parent $resolvedOutput
  if ($outputDir -and -not (Test-Path $outputDir)) {
    New-Item -Path $outputDir -ItemType Directory -Force | Out-Null
  }

  $windowSize = "$Width,$Height"
  $tempProfileRoot = Join-Path ([IO.Path]::GetTempPath()) "float-headless"
  if (-not (Test-Path $tempProfileRoot)) {
    New-Item -Path $tempProfileRoot -ItemType Directory -Force | Out-Null
  }
  $tempProfile = Join-Path $tempProfileRoot ("profile-" + [Guid]::NewGuid().ToString("N"))
  New-Item -Path $tempProfile -ItemType Directory -Force | Out-Null
  try {
    & $browser "--headless=new" "--disable-gpu" "--hide-scrollbars" "--no-first-run" "--no-default-browser-check" "--user-data-dir=$tempProfile" "--virtual-time-budget=$VirtualTimeMs" "--window-size=$windowSize" "--screenshot=$resolvedOutput" $targetUrl
  } finally {
    Remove-Item -Path $tempProfile -Recurse -Force -ErrorAction SilentlyContinue
  }

  if (-not (Test-Path $resolvedOutput)) {
    throw "Browser command completed but screenshot was not written: $resolvedOutput"
  }

  Write-Host "Saved screenshot: $resolvedOutput"
  Write-Host "Target URL: $targetUrl"
  Write-Host "Browser: $browser"
} finally {
  Pop-Location
}
