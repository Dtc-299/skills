param([string]$TargetRoot = '', [switch]$Update)
$ErrorActionPreference = 'Stop'
if (-not $TargetRoot) {
    $dtcHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $TargetRoot = Join-Path $dtcHome 'skills'
}
$dtcDestinationRoot = [IO.Path]::GetFullPath($TargetRoot)
$dtcSourceRoot = Join-Path $PSScriptRoot 'skills'
$dtcNames = @('dtc1', 'dtc2')
$dtcPlan = @()
$dtcBackupRoot = Join-Path (Split-Path -Parent $dtcDestinationRoot) ('skill-backups\dtc-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
foreach ($dtcName in $dtcNames) {
    $dtcSource = Join-Path $dtcSourceRoot $dtcName
    $dtcDestination = Join-Path $dtcDestinationRoot $dtcName
    if (-not (Test-Path -LiteralPath (Join-Path $dtcSource 'SKILL.md'))) { throw "Missing SKILL.md: $dtcName" }
    if (Test-Path -LiteralPath $dtcDestination) {
        $dtcExisting = Get-Item -LiteralPath $dtcDestination -Force
        if ($dtcExisting.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse-point target: $dtcDestination" }
        foreach ($dtcChild in (Get-ChildItem -LiteralPath $dtcDestination -Recurse -Force)) {
            if ($dtcChild.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse-point child: $($dtcChild.FullName)" }
        }
    }
    foreach ($dtcFile in (Get-ChildItem -LiteralPath $dtcSource -Recurse -File)) {
        $dtcRelative = $dtcFile.FullName.Substring($dtcSource.Length).TrimStart('\','/')
        $dtcTarget = Join-Path $dtcDestination $dtcRelative
        $dtcHash = (Get-FileHash -LiteralPath $dtcFile.FullName -Algorithm SHA256).Hash
        $dtcChanged = $false
        if (Test-Path -LiteralPath $dtcTarget) {
            $dtcInstalledHash = (Get-FileHash -LiteralPath $dtcTarget -Algorithm SHA256).Hash
            $dtcChanged = $dtcHash -ne $dtcInstalledHash
            if ($dtcChanged -and -not $Update) { throw "Existing skill differs. Use -Update to back up replaced files before upgrading: $dtcTarget" }
        }
        $dtcPlan += [PSCustomObject]@{ Source=$dtcFile.FullName; Target=$dtcTarget; Hash=$dtcHash; Changed=$dtcChanged; Backup=(Join-Path (Join-Path $dtcBackupRoot $dtcName) $dtcRelative) }
    }
}
foreach ($dtcEntry in $dtcPlan) {
    $dtcParent = Split-Path -Parent $dtcEntry.Target
    New-Item -ItemType Directory -Path $dtcParent -Force | Out-Null
    if ($dtcEntry.Changed) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $dtcEntry.Backup) -Force | Out-Null
        Copy-Item -LiteralPath $dtcEntry.Target -Destination $dtcEntry.Backup
        if ((Get-FileHash -LiteralPath $dtcEntry.Backup -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $dtcEntry.Target -Algorithm SHA256).Hash) { throw "Backup verification failed" }
    }
    if ($dtcEntry.Changed -or -not (Test-Path -LiteralPath $dtcEntry.Target)) {
        Copy-Item -LiteralPath $dtcEntry.Source -Destination $dtcEntry.Target -Force
    }
    if ((Get-FileHash -LiteralPath $dtcEntry.Target -Algorithm SHA256).Hash -ne $dtcEntry.Hash) {
        throw "Installed file verification failed: $($dtcEntry.Target)"
    }
}
$dtcBackupResult = if (Test-Path -LiteralPath $dtcBackupRoot) { $dtcBackupRoot } else { $null }
[PSCustomObject]@{ Status='installed-and-hash-verified'; Skills=$dtcNames; Target=$dtcDestinationRoot; Files=$dtcPlan.Count; Backup=$dtcBackupResult } | ConvertTo-Json
