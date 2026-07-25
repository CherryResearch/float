<#
.SYNOPSIS
Captures a Float UI route with Chromium.

.DESCRIPTION
Uses the existing width and height parameters for custom captures. The Pixel9
device profile applies Chromium's Pixel 9 viewport metrics, touch input, an
Android mobile user agent, and the matching device scale factor. The helper
requires a Node.js runtime with global WebSocket support (Node.js 22 or newer is
recommended).

.PARAMETER DeviceProfile
Optional named device profile. Pixel9 uses a 412x924 CSS-pixel portrait viewport
at DPR 2.625.

.PARAMETER Landscape
Uses the Pixel9 profile in landscape at 924x412 CSS pixels. This switch requires
-DeviceProfile Pixel9.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/capture_ui.ps1 -DeviceProfile Pixel9

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/capture_ui.ps1 -DeviceProfile Pixel9 -Landscape
#>
param(
  [string]$OutputPath = "data/screenshots/ui.png",
  [string]$Route = "/?tab=threads",
  [int]$Width = 1440,
  [int]$Height = 900,
  [ValidateSet("None", "Pixel9")][string]$DeviceProfile = "None",
  [switch]$Landscape,
  [int]$VirtualTimeMs = 20000,
  [int]$TimeoutSec = 90,
  [int]$FrontendPort = 0,
  [int]$BackendPort = 0
)

$ErrorActionPreference = "Stop"

$captureProfile = "Custom"
$devicePixelRatio = $null
if ($DeviceProfile -eq "Pixel9") {
  if ($PSBoundParameters.ContainsKey("Width") -or $PSBoundParameters.ContainsKey("Height")) {
    throw "-Width and -Height cannot be combined with -DeviceProfile Pixel9. Use the profile dimensions or omit -DeviceProfile for a custom viewport."
  }

  $captureProfile = if ($Landscape) { "Pixel 9 (landscape)" } else { "Pixel 9 (portrait)" }
  $Width = if ($Landscape) { 924 } else { 412 }
  $Height = if ($Landscape) { 412 } else { 924 }
  $devicePixelRatio = 2.625
} elseif ($Landscape) {
  throw "-Landscape requires -DeviceProfile Pixel9."
}

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

function Get-Pixel9UserAgent {
  param(
    [Parameter(Mandatory = $true)][string]$BrowserPath
  )
  $browserVersion = (Get-Item -LiteralPath $BrowserPath).VersionInfo.ProductVersion
  if (-not $browserVersion -or $browserVersion -notmatch "^(\d+)") {
    throw "Could not determine the Chromium version for the Pixel9 mobile user agent: $BrowserPath"
  }
  $chromiumMajor = $Matches[1]
  return "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/$chromiumMajor.0.0.0 Mobile Safari/537.36"
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
  $mobileUserAgent = if ($DeviceProfile -eq "Pixel9") {
    Get-Pixel9UserAgent -BrowserPath $browser
  } else {
    $null
  }
  $browserOutput = $null
  $deviceMetrics = $null
  if ($DeviceProfile -eq "Pixel9") {
    $node = (Get-Command node -ErrorAction Stop).Source
    $captureHelper = Join-Path $PSScriptRoot "capture_ui_chromium.mjs"
    if (-not (Test-Path -LiteralPath $captureHelper)) {
      throw "Pixel9 capture helper is missing: $captureHelper"
    }
    $browserOutput = & $node $captureHelper `
      "--browser" $browser `
      "--url" $targetUrl `
      "--output" $resolvedOutput `
      "--width" $Width `
      "--height" $Height `
      "--dpr" $devicePixelRatio `
      "--ua" $mobileUserAgent `
      "--timeout-ms" ($TimeoutSec * 1000) `
      "--settle-ms" ([Math]::Min($VirtualTimeMs, 5000)) 2>&1
    $browserExitCode = $LASTEXITCODE
    if ($browserExitCode -eq 0 -and $browserOutput) {
      try {
        $deviceMetrics = (($browserOutput | Out-String).Trim() | ConvertFrom-Json)
      } catch {
        throw "Pixel9 capture completed without readable device metrics: $browserOutput"
      }
    }
  } else {
    $tempProfileRoot = Join-Path ([IO.Path]::GetTempPath()) "float-headless"
    if (-not (Test-Path $tempProfileRoot)) {
      New-Item -Path $tempProfileRoot -ItemType Directory -Force | Out-Null
    }
    $tempProfile = Join-Path $tempProfileRoot ("profile-" + [Guid]::NewGuid().ToString("N"))
    New-Item -Path $tempProfile -ItemType Directory -Force | Out-Null
    try {
    $browserArgs = @(
      "--headless=new"
      "--disable-gpu"
      "--hide-scrollbars"
      "--no-first-run"
      "--no-default-browser-check"
      "--user-data-dir=$tempProfile"
      "--virtual-time-budget=$VirtualTimeMs"
      "--window-size=$windowSize"
      "--screenshot=$resolvedOutput"
    )
    $browserOutput = & $browser @browserArgs $targetUrl 2>&1
    $browserExitCode = $LASTEXITCODE
    } finally {
      Remove-Item -Path $tempProfile -Recurse -Force -ErrorAction SilentlyContinue
    }
  }

  if ($browserExitCode -ne 0) {
    $browserDetails = if ($browserOutput) {
      ($browserOutput | Out-String).Trim()
    } else {
      "(no browser output)"
    }
    throw "Browser screenshot command failed with exit code $browserExitCode.`nBrowser: $browser`nTarget URL: $targetUrl`nOutput: $browserDetails"
  }

  if (-not (Test-Path $resolvedOutput)) {
    $browserDetails = if ($browserOutput) {
      ($browserOutput | Out-String).Trim()
    } else {
      "(no browser output)"
    }
    throw "Browser command completed but screenshot was not written: $resolvedOutput`nBrowser: $browser`nTarget URL: $targetUrl`nOutput: $browserDetails"
  }

  Write-Host "Saved screenshot: $resolvedOutput"
  Write-Host "Target URL: $targetUrl"
  Write-Host "Browser: $browser"
  Write-Host "Capture profile: $captureProfile"
  Write-Host "Logical viewport: ${Width}x${Height} CSS px"
  if ($devicePixelRatio) {
    $physicalWidth = [Math]::Round($Width * $devicePixelRatio)
    $physicalHeight = [Math]::Round($Height * $devicePixelRatio)
    Write-Host "Device pixel ratio: $devicePixelRatio"
    Write-Host "Expected physical raster: ${physicalWidth}x${physicalHeight} px (rounded)"
    if ($deviceMetrics) {
      Write-Host "Observed viewport: $($deviceMetrics.innerWidth)x$($deviceMetrics.innerHeight) CSS px"
      Write-Host "Observed DPR: $($deviceMetrics.devicePixelRatio); coarse pointer: $($deviceMetrics.pointerCoarse); hover none: $($deviceMetrics.hoverNone)"
      Write-Host "Document width: client $($deviceMetrics.document.clientWidth), scroll $($deviceMetrics.document.scrollWidth)"
    }
  } else {
    Write-Host "Device pixel ratio: browser default"
    Write-Host "Expected physical raster: browser default for ${Width}x${Height} window"
  }
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
} finally {
  Pop-Location
}
