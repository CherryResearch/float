param(
  [int]$BackendPort = 0,
  [int]$FrontendPort = 0,
  [int]$ServePort = 64345,
  [int]$TimeoutSec = 90,
  [string]$TailnetHost = "",
  [string[]]$AllowedHost = @(),
  [switch]$StickyPorts,
  [switch]$WithWorker,
  [switch]$WithRedis,
  [switch]$Stop,
  [switch]$RequireProviderReady,
  [switch]$ChatProbe
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StatePath = Join-Path $RepoRoot ".dev_state.json"

function Write-Step {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-Host "[mobile-float] $Message"
}

function Get-UniqueValues {
  param([string[]]$Values)
  $seen = @{}
  $result = @()
  foreach ($value in $Values) {
    $trimmed = ([string]$value).Trim()
    if (-not $trimmed) {
      continue
    }
    $key = $trimmed.ToLowerInvariant()
    if (-not $seen.ContainsKey($key)) {
      $seen[$key] = $true
      $result += $trimmed
    }
  }
  return $result
}

function Get-LauncherState {
  if (-not (Test-Path $StatePath)) {
    return $null
  }
  try {
    return Get-Content $StatePath -Raw | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Set-LauncherStateStopped {
  param($State)
  if (-not $State) {
    return
  }
  try {
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $State | Add-Member -NotePropertyName "launcher_running" -NotePropertyValue $false -Force
    $State | Add-Member -NotePropertyName "updated_at_epoch" -NotePropertyValue $now -Force
    $State | Add-Member -NotePropertyName "shutdown_requested_at_epoch" -NotePropertyValue $now -Force
    if ($State.processes) {
      $State.processes.PSObject.Properties | ForEach-Object {
        if ($_.Value) {
          $_.Value | Add-Member -NotePropertyName "running" -NotePropertyValue $false -Force
        }
      }
    }
    $State | ConvertTo-Json -Depth 10 | Set-Content -Path $StatePath -Encoding UTF8
  } catch {
    Write-Warning "Could not mark .dev_state.json stopped: $($_.Exception.Message)"
  }
}

function Get-TailscaleSelf {
  $tailscale = Get-Command tailscale -ErrorAction Stop
  $raw = & $tailscale.Source status --self --json
  if ($LASTEXITCODE -ne 0 -or -not $raw) {
    throw "Tailscale is not running or is not logged in."
  }
  return $raw | ConvertFrom-Json
}

function Wait-HttpReady {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][int]$WaitSeconds
  )
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  $lastError = $null
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 6
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
        Write-Step "$Label ready ($($response.StatusCode))"
        return $response
      }
      $lastError = "HTTP $($response.StatusCode)"
    } catch {
      $lastError = $_.Exception.Message
    }
    Start-Sleep -Milliseconds 700
  }
  throw "Timed out waiting for ${Label}: ${Url}. Last error: ${lastError}"
}

function Stop-ProcessTree {
  param([int]$RootPid)
  if ($RootPid -le 0) {
    return
  }
  $all = Get-CimInstance Win32_Process
  $ids = @($RootPid)
  $changed = $true
  while ($changed) {
    $changed = $false
    foreach ($process in $all) {
      if (($ids -contains $process.ParentProcessId) -and -not ($ids -contains $process.ProcessId)) {
        $ids += $process.ProcessId
        $changed = $true
      }
    }
  }
  foreach ($id in ($ids | Sort-Object -Descending -Unique)) {
    try {
      Stop-Process -Id $id -Force -ErrorAction Stop
    } catch {
      # Process may have already exited.
    }
  }
}

function Stop-MobileFloat {
  param([int]$Port)
  try {
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      $serveOutput = & tailscale serve "--http=$Port" off 2>&1
      $serveExit = $LASTEXITCODE
      if ($serveExit -eq 0) {
        Write-Step "stopped Tailscale Serve on :$Port"
      } else {
        $statusOutput = & tailscale serve status 2>$null
        $statusExit = $LASTEXITCODE
        if ($statusExit -eq 0 -and (($statusOutput -join "`n") -match "No serve config")) {
          Write-Step "Tailscale Serve already stopped"
        } else {
          Write-Warning "Could not stop Tailscale Serve on :${Port}: $($serveOutput -join ' ')"
        }
      }
    } finally {
      $ErrorActionPreference = $previousErrorPreference
    }
  } catch {
    Write-Warning "Could not stop Tailscale Serve on :${Port}: $($_.Exception.Message)"
  }
  $state = Get-LauncherState
  $roots = @()
  if ($state) {
    if ($state.launcher_pid) {
      $roots += [int]$state.launcher_pid
    }
    if ($state.processes) {
      $state.processes.PSObject.Properties | ForEach-Object {
        if ($_.Value.pid) {
          $roots += [int]$_.Value.pid
        }
      }
    }
  }
  foreach ($root in ($roots | Sort-Object -Unique)) {
    Stop-ProcessTree -RootPid $root
  }
  Set-LauncherStateStopped -State $state
  Write-Step "stop requested for Mobile Float launcher processes"
}

function Wait-FloatPorts {
  param(
    [double]$StartedAfterEpoch,
    [int]$WaitSeconds
  )
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  while ((Get-Date) -lt $deadline) {
    $state = Get-LauncherState
    if ($state) {
      $updated = if ($state.updated_at_epoch) { [double]$state.updated_at_epoch } else { 0.0 }
      $backend = if ($BackendPort -gt 0) { $BackendPort } else { [int]($state.backend_port) }
      $frontend = if ($FrontendPort -gt 0) { $FrontendPort } else { [int]($state.frontend_port) }
      if ($updated -ge $StartedAfterEpoch -and $backend -gt 0 -and $frontend -gt 0) {
        try {
          Wait-HttpReady -Url "http://127.0.0.1:$backend/health" -Label "backend health" -WaitSeconds 2 | Out-Null
          Wait-HttpReady -Url "http://localhost:$frontend/" -Label "frontend" -WaitSeconds 2 | Out-Null
          return [pscustomobject]@{
            BackendPort = $backend
            FrontendPort = $frontend
          }
        } catch {
          # Keep waiting for both sides to become reachable.
        }
      }
    }
    Start-Sleep -Milliseconds 800
  }
  throw "Timed out waiting for Mobile Float backend/frontend ports."
}

function Test-ProviderReady {
  param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [switch]$Require
  )
  try {
    $settingsResponse = Wait-HttpReady -Url "$BaseUrl/api/settings" -Label "backend settings via mobile URL" -WaitSeconds 12
    $settings = $settingsResponse.Content | ConvertFrom-Json
    $mode = [string]$settings.mode
    Write-Step "configured model mode: $mode"
    if ($mode -eq "api") {
      if (-not $settings.api_key_set) {
        throw "API mode is selected, but no API key is configured."
      }
      Wait-HttpReady -Url "$BaseUrl/api/openai/models" -Label "API model listing" -WaitSeconds 20 | Out-Null
      return
    }
    if ($mode -match "local|dynamic|hybrid") {
      Wait-HttpReady -Url "$BaseUrl/api/llm/provider/status" -Label "local provider status" -WaitSeconds 12 | Out-Null
      return
    }
    Write-Warning "Provider readiness probe does not know mode '$mode'; page/backend are reachable."
  } catch {
    $message = "Provider readiness probe failed: $($_.Exception.Message)"
    if ($Require) {
      throw $message
    }
    Write-Warning "$message. The mobile page may load while chat messages fail."
  }
}

function Invoke-ChatProbe {
  param([Parameter(Mandatory = $true)][string]$BaseUrl)
  $sessionId = "mobile-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss")
  $body = @{
    message = "Reply with exactly PONG."
    session_id = $sessionId
    use_rag = $false
    use_text_rag = $false
    use_vision_rag = $false
    patience = 1
  } | ConvertTo-Json -Depth 4
  $response = Invoke-WebRequest -Uri "$BaseUrl/api/chat" -UseBasicParsing -TimeoutSec 120 -Method Post -ContentType "application/json" -Body $body
  if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
    throw "Chat probe failed with HTTP $($response.StatusCode)."
  }
  Write-Step "chat probe returned HTTP $($response.StatusCode)"
}

Set-Location $RepoRoot

if ($Stop) {
  Stop-MobileFloat -Port $ServePort
  return
}

$tailscaleSelf = Get-TailscaleSelf
if (-not $TailnetHost) {
  $TailnetHost = ([string]$tailscaleSelf.Self.DNSName).TrimEnd(".")
}
if (-not $TailnetHost) {
  throw "Could not determine this machine's Tailscale DNS name. Pass -TailnetHost explicitly."
}

$shortHost = if ($tailscaleSelf.Self.HostName) { [string]$tailscaleSelf.Self.HostName } else { $env:COMPUTERNAME }
$viteAllowed = Get-UniqueValues (@($TailnetHost, $shortHost) + $AllowedHost + (($env:VITE_ALLOWED_HOSTS -split ",") | Where-Object { $_ }))
$allowedText = ($viteAllowed -join ",")

$launcherArgs = @("run", "float", "--no-open")
if ($BackendPort -gt 0) {
  $launcherArgs += @("--backend-port", [string]$BackendPort)
}
if ($FrontendPort -gt 0) {
  $launcherArgs += @("--frontend-port", [string]$FrontendPort)
}
if (-not $StickyPorts) {
  $launcherArgs += "--no-sticky-ports"
}
if ($WithWorker) {
  $launcherArgs += "--with-worker"
} else {
  $launcherArgs += "--no-worker"
}
if ($WithRedis) {
  $launcherArgs += "--with-redis"
} else {
  $launcherArgs += "--no-redis"
}

$startedAfter = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
$phoneUrl = "http://${TailnetHost}:$ServePort"
$launcherText = ($launcherArgs | ForEach-Object {
  if ($_ -match '\s') { "'$($_.Replace("'", "''"))'" } else { $_ }
}) -join " "
$command = @"
`$Host.UI.RawUI.WindowTitle = 'Mobile Float'
Set-Location '$($RepoRoot.Replace("'", "''"))'
`$env:VITE_ALLOWED_HOSTS = '$($allowedText.Replace("'", "''"))'
Write-Host '[mobile-float] visible launcher terminal'
Write-Host '[mobile-float] mobile URL will be: $phoneUrl/'
Write-Host '[mobile-float] running: poetry $launcherText'
poetry $launcherText
"@

Write-Step "starting visible Mobile Float terminal"
Write-Step "Vite allowed hosts: $allowedText"
$terminal = Start-Process -FilePath "powershell.exe" -ArgumentList @(
  "-NoExit",
  "-ExecutionPolicy",
  "Bypass",
  "-Command",
  $command
) -WorkingDirectory $RepoRoot -WindowStyle Normal -PassThru

$ports = Wait-FloatPorts -StartedAfterEpoch $startedAfter -WaitSeconds $TimeoutSec
Write-Step "backend port: $($ports.BackendPort); frontend port: $($ports.FrontendPort)"

$target = "localhost:$($ports.FrontendPort)"
Write-Step "starting Tailscale Serve on :$ServePort -> $target"
& tailscale serve "--http=$ServePort" --bg --yes $target | Write-Host
if ($LASTEXITCODE -ne 0) {
  throw "tailscale serve failed."
}

Wait-HttpReady -Url "$phoneUrl/" -Label "mobile frontend" -WaitSeconds 20 | Out-Null
Wait-HttpReady -Url "$phoneUrl/health" -Label "mobile backend health proxy" -WaitSeconds 20 | Out-Null
Wait-HttpReady -Url "$phoneUrl/api/tools/catalog" -Label "mobile backend API proxy" -WaitSeconds 20 | Out-Null
Test-ProviderReady -BaseUrl $phoneUrl -Require:$RequireProviderReady
if ($ChatProbe) {
  Invoke-ChatProbe -BaseUrl $phoneUrl
}

Write-Host ""
Write-Host "Mobile Float URL: $phoneUrl/"
Write-Host "Visible terminal PID: $($terminal.Id)"
