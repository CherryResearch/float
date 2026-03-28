param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("find-paths", "find-text", "head", "range")]
    [string]$Mode,

    [string]$Roots = ".",
    [string]$Pattern = "",
    [string]$PathPattern = "",
    [int]$Count = 40,
    [string]$File = "",
    [int]$Start = 1,
    [int]$End = 40
)

$ErrorActionPreference = "Stop"
$RootList = $Roots -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }

switch ($Mode) {
    "find-paths" {
        Get-ChildItem -Path $RootList -Recurse -File |
            Where-Object { $_.FullName -match $PathPattern } |
            ForEach-Object { $_.FullName }
    }
    "find-text" {
        Get-ChildItem -Path $RootList -Recurse -File |
            Select-String -Pattern $Pattern |
            ForEach-Object {
                "{0}:{1}:{2}" -f $_.Path, $_.LineNumber, $_.Line.Trim()
            }
    }
    "head" {
        Get-Content -Path $File -TotalCount $Count
    }
    "range" {
        $lines = Get-Content -Path $File
        for ($i = $Start; $i -le $End -and $i -le $lines.Length; $i++) {
            "{0}:{1}" -f $i, $lines[$i - 1]
        }
    }
}
