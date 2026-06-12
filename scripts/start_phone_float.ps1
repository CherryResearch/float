param(
  [int]$BackendPort = 0,
  [int]$FrontendPort = 0,
  [int]$ServePort = 64345,
  [int]$TimeoutSec = 90,
  [string]$TailnetHost = "",
  [string[]]$AllowedHost = @(),
  [switch]$StickyPorts,
  [switch]$NoStickyPorts,
  [switch]$WithWorker,
  [switch]$WithRedis,
  [switch]$NoServe,
  [switch]$NoWait,
  [switch]$Stop,
  [switch]$RequireProviderReady,
  [switch]$ChatProbe
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "logs"
$StatePath = Join-Path $RepoRoot ".dev_state.json"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-Host "[phone-float] $Message"
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

function Wait-FloatPorts {
  param(
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Launcher,
    [Parameter(Mandatory = $true)][int]$WaitSeconds
  )
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($Launcher.HasExited) {
      throw "Float launcher exited early with code $($Launcher.ExitCode). Check the log files printed above."
    }
    $state = Get-LauncherState
    $backend = if ($BackendPort -gt 0) { $BackendPort } else { [int]($state.backend_port) }
    $frontend = if ($FrontendPort -gt 0) { $FrontendPort } else { [int]($state.frontend_port) }
    if ($backend -gt 0 -and $frontend -gt 0) {
      try {
        Wait-HttpReady -Url "http://127.0.0.1:$backend/health" -Label "backend health" -WaitSeconds 2 | Out-Null
        Wait-HttpReady -Url "http://localhost:$frontend/" -Label "frontend" -WaitSeconds 2 | Out-Null
        return [pscustomobject]@{
          BackendPort = $backend
          FrontendPort = $frontend
        }
      } catch {
        # Keep waiting until both services are actually reachable.
      }
    }
    Start-Sleep -Milliseconds 800
  }
  throw "Timed out waiting for Float backend/frontend ports."
}

function Test-ProviderReady {
  param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [switch]$Require
  )
  try {
    $settingsResponse = Wait-HttpReady -Url "$BaseUrl/api/settings" -Label "backend settings via phone URL" -WaitSeconds 12
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
    Write-Warning "$message. The phone page may load while chat messages fail."
  }
}

function Invoke-ChatProbe {
  param([Parameter(Mandatory = $true)][string]$BaseUrl)
  $sessionId = "phone-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss")
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
  foreach ($id in ($ids | Sort-Object -Descending)) {
    try {
      Stop-Process -Id $id -Force -ErrorAction Stop
    } catch {
      # Process may have already exited.
    }
  }
}

Set-Location $RepoRoot

if ($Stop) {
  if (-not $NoServe) {
    try {
      & tailscale serve "--http=$ServePort" off | Out-Null
      Write-Step "stopped Tailscale Serve on :$ServePort"
    } catch {
      Write-Warning "Could not stop Tailscale Serve on :${ServePort}: $($_.Exception.Message)"
    }
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
  Write-Step "stop requested for Float phone launcher processes"
  return
}

$tailscaleSelf = $null
if (-not $NoServe) {
  $tailscaleSelf = Get-TailscaleSelf
  if (-not $TailnetHost) {
    $TailnetHost = ([string]$tailscaleSelf.Self.DNSName).TrimEnd(".")
  }
  if (-not $TailnetHost) {
    throw "Could not determine this machine's Tailscale DNS name. Pass -TailnetHost explicitly."
  }
}

$shortHost = if ($tailscaleSelf -and $tailscaleSelf.Self.HostName) { [string]$tailscaleSelf.Self.HostName } else { $env:COMPUTERNAME }
$viteAllowed = Get-UniqueValues (@($TailnetHost, $shortHost) + $AllowedHost + (($env:VITE_ALLOWED_HOSTS -split ",") | Where-Object { $_ }))
$env:VITE_ALLOWED_HOSTS = ($viteAllowed -join ",")
Write-Step "Vite allowed hosts: $env:VITE_ALLOWED_HOSTS"

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $LogDir "phone_float_$timestamp.out.log"
$stderrLog = Join-Path $LogDir "phone_float_$timestamp.err.log"

$launcherArgs = @("run", "float", "--no-open")
if ($BackendPort -gt 0) {
  $launcherArgs += @("--backend-port", [string]$BackendPort)
}
if ($FrontendPort -gt 0) {
  $launcherArgs += @("--frontend-port", [string]$FrontendPort)
}
# Phone access goes through the stable Tailscale Serve port, so avoid stale
# launcher sticky ports unless the caller explicitly asks to reuse them.
if ($NoStickyPorts -or -not $StickyPorts) {
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

$poetry = Get-Command poetry -ErrorAction Stop
Write-Step "starting Float launcher"
Write-Step "stdout: $stdoutLog"
Write-Step "stderr: $stderrLog"
$launcher = Start-Process -FilePath $poetry.Source -ArgumentList $launcherArgs -WorkingDirectory $RepoRoot -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru -WindowStyle Hidden

$ports = $null
$phoneUrl = $null
try {
  $ports = Wait-FloatPorts -Launcher $launcher -WaitSeconds $TimeoutSec
  Write-Step "backend port: $($ports.BackendPort); frontend port: $($ports.FrontendPort)"

  if (-not $NoServe) {
    $target = "localhost:$($ports.FrontendPort)"
    Write-Step "starting Tailscale Serve on :$ServePort -> $target"
    & tailscale serve "--http=$ServePort" --bg --yes $target | Write-Host
    if ($LASTEXITCODE -ne 0) {
      throw "tailscale serve failed."
    }
    $phoneUrl = "http://${TailnetHost}:$ServePort"
    Wait-HttpReady -Url "$phoneUrl/" -Label "phone frontend" -WaitSeconds 20 | Out-Null
    Wait-HttpReady -Url "$phoneUrl/health" -Label "phone backend health proxy" -WaitSeconds 20 | Out-Null
    Wait-HttpReady -Url "$phoneUrl/api/tools/catalog" -Label "phone backend API proxy" -WaitSeconds 20 | Out-Null
    Test-ProviderReady -BaseUrl $phoneUrl -Require:$RequireProviderReady
    if ($ChatProbe) {
      Invoke-ChatProbe -BaseUrl $phoneUrl
    }
    Write-Host ""
    Write-Host "Phone URL: $phoneUrl/"
  } else {
    Write-Host ""
    Write-Host "Local frontend URL: http://localhost:$($ports.FrontendPort)/"
  }

  if ($NoWait) {
    Write-Step "leaving Float running because -NoWait was provided"
    if (-not $NoServe) {
      Write-Step "stop tailnet access with: tailscale serve --http=$ServePort off"
    }
    return
  }

  Write-Step "Float is running. Press Ctrl+C in this terminal to stop it."
  Wait-Process -Id $launcher.Id
} finally {
  if (-not $NoWait) {
    if (-not $NoServe) {
      try {
        & tailscale serve "--http=$ServePort" off | Out-Null
      } catch {
        Write-Warning "Could not stop Tailscale Serve: $($_.Exception.Message)"
      }
    }
    Stop-ProcessTree -RootPid $launcher.Id
  }
}
