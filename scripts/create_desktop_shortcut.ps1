param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$ShortcutName = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

function New-FloatIcon {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePng,
        [Parameter(Mandatory = $true)]
        [string]$TargetIco,
        [int]$TargetSize = 256
    )

    $sourceBitmap = $null
    $iconBitmap = $null
    $graphics = $null
    $pngStream = $null
    $stream = $null
    $writer = $null

    try {
        $sourceBitmap = New-Object System.Drawing.Bitmap($SourcePng)
        $iconBitmap = New-Object System.Drawing.Bitmap($TargetSize, $TargetSize, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($iconBitmap)
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality

        $scale = [Math]::Min($TargetSize / $sourceBitmap.Width, $TargetSize / $sourceBitmap.Height)
        $drawWidth = [int][Math]::Round($sourceBitmap.Width * $scale)
        $drawHeight = [int][Math]::Round($sourceBitmap.Height * $scale)
        $drawX = [int][Math]::Floor(($TargetSize - $drawWidth) / 2)
        $drawY = [int][Math]::Floor(($TargetSize - $drawHeight) / 2)

        $graphics.DrawImage($sourceBitmap, $drawX, $drawY, $drawWidth, $drawHeight)

        $pngStream = New-Object System.IO.MemoryStream
        $iconBitmap.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $pngStream.ToArray()

        $stream = [System.IO.File]::Open($TargetIco, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
        $writer = New-Object System.IO.BinaryWriter($stream)

        $writer.Write([UInt16]0) # ICONDIR.Reserved
        $writer.Write([UInt16]1) # ICONDIR.Type (1 = icon)
        $writer.Write([UInt16]1) # ICONDIR.Count

        $sizeByte = if ($TargetSize -ge 256) { [byte]0 } else { [byte]$TargetSize }
        $writer.Write($sizeByte)   # Width
        $writer.Write($sizeByte)   # Height
        $writer.Write([byte]0)     # ColorCount
        $writer.Write([byte]0)     # Reserved
        $writer.Write([UInt16]1)   # Planes
        $writer.Write([UInt16]32)  # BitCount
        $writer.Write([UInt32]$pngBytes.Length) # BytesInRes
        $writer.Write([UInt32]22)  # ImageOffset (6 + 16)
        $writer.Write($pngBytes)
        $writer.Flush()
    }
    finally {
        if ($writer) {
            $writer.Dispose()
        }
        if ($stream) {
            $stream.Dispose()
        }
        if ($pngStream) {
            $pngStream.Dispose()
        }
        if ($graphics) {
            $graphics.Dispose()
        }
        if ($iconBitmap) {
            $iconBitmap.Dispose()
        }
        if ($sourceBitmap) {
            $sourceBitmap.Dispose()
        }
    }
}

function Resolve-FloatLauncher {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRootPath
    )

    $poetryCandidates = @()
    $poetryCommand = Get-Command poetry -ErrorAction SilentlyContinue
    if ($poetryCommand -and $poetryCommand.Path) {
        $poetryCandidates += $poetryCommand.Path
    }

    $poetryCandidates += (Join-Path $env:USERPROFILE ".local\bin\poetry.exe")
    $poetryCandidates += (Join-Path $env:APPDATA "pypoetry\venv\Scripts\poetry.exe")
    $poetryCandidates += (Join-Path $env:APPDATA "Python\Scripts\poetry.exe")
    $poetryCandidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\Scripts\poetry.exe")

    $poetryPath = $poetryCandidates |
        Where-Object { $_ -and (Test-Path $_) } |
        Select-Object -First 1

    if ($poetryPath) {
        return @{
            TargetPath = $poetryPath
            Arguments = "run float"
            WorkingDirectory = $ProjectRootPath
            LaunchSummary = "`"$poetryPath`" run float"
        }
    }

    $pyCandidates = @()
    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand -and $pyCommand.Path) {
        $pyCandidates += $pyCommand.Path
    }
    $pyCandidates += (Join-Path $env:SystemRoot "py.exe")

    $pyPath = $pyCandidates |
        Where-Object { $_ -and (Test-Path $_) } |
        Select-Object -First 1

    if ($pyPath) {
        return @{
            TargetPath = $pyPath
            Arguments = "-m poetry run float"
            WorkingDirectory = $ProjectRootPath
            LaunchSummary = "`"$pyPath`" -m poetry run float"
        }
    }

    throw "Could not find Poetry launcher. Install Poetry or ensure poetry.exe is available."
}

$projectRootPath = (Resolve-Path $ProjectRoot).Path
$shortcutBaseName = if ([string]::IsNullOrWhiteSpace($ShortcutName)) {
    Split-Path $projectRootPath -Leaf
} else {
    $ShortcutName.Trim()
}
if ([string]::IsNullOrWhiteSpace($shortcutBaseName)) {
    $shortcutBaseName = "float"
}
$preferredPngIconPath = Join-Path $projectRootPath "docs\resources\floatlogo.png"
$secondaryPngIconPath = Join-Path $projectRootPath "docs\resources\floatgpt.png"
$fallbackPngIconPath = Join-Path $projectRootPath "frontend\public\floatgpt.png"
$pngIconPath = $preferredPngIconPath
if (-not (Test-Path $pngIconPath)) {
    $pngIconPath = if (Test-Path $secondaryPngIconPath) { $secondaryPngIconPath } else { $fallbackPngIconPath }
}
$icoIconPath = Join-Path $projectRootPath "frontend\public\float.ico"
$desktopPath = [Environment]::GetFolderPath("DesktopDirectory")
$shortcutPath = Join-Path $desktopPath "$shortcutBaseName.lnk"
$legacyShortcutPath = Join-Path $desktopPath "float.lnk"

if (-not (Test-Path $pngIconPath)) {
    throw "Logo source not found at any expected path: $preferredPngIconPath, $secondaryPngIconPath, or $fallbackPngIconPath"
}

$regenerateIcon = $true
if (Test-Path $icoIconPath) {
    $pngMtime = (Get-Item $pngIconPath).LastWriteTimeUtc
    $icoMtime = (Get-Item $icoIconPath).LastWriteTimeUtc
    $regenerateIcon = $pngMtime -gt $icoMtime
    if (-not $regenerateIcon) {
        try {
            $iconCheck = New-Object System.Drawing.Icon($icoIconPath)
            $iconCheck.Dispose()
        }
        catch {
            $regenerateIcon = $true
        }
    }
}
if ($regenerateIcon) {
    New-FloatIcon -SourcePng $pngIconPath -TargetIco $icoIconPath
}

$launcher = Resolve-FloatLauncher -ProjectRootPath $projectRootPath

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher.TargetPath
$shortcut.Arguments = $launcher.Arguments
$shortcut.WorkingDirectory = $launcher.WorkingDirectory
$shortcut.Description = $shortcutBaseName
$shortcut.IconLocation = "$icoIconPath,0"
$shortcut.WindowStyle = 1
$shortcut.Save()

if ($shortcutPath -ne $legacyShortcutPath -and (Test-Path $legacyShortcutPath)) {
    $legacyShortcut = $shell.CreateShortcut($legacyShortcutPath)
    $legacyWorkingDirectory = $legacyShortcut.WorkingDirectory
    if (-not $legacyWorkingDirectory -or -not (Test-Path $legacyWorkingDirectory) -or $legacyWorkingDirectory -eq $projectRootPath) {
        Remove-Item $legacyShortcutPath -Force
        Write-Host "[INFO] Removed stale desktop shortcut: $legacyShortcutPath"
    }
}

Write-Host "[INFO] Created desktop shortcut: $shortcutPath"
Write-Host "[INFO] Icon source: $pngIconPath"
Write-Host "[INFO] Icon file: $icoIconPath"
Write-Host "[INFO] Launch command: $($launcher.LaunchSummary)"
