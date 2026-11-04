# Compiles the Windows installer with Inno Setup (installer.iss). Used by the launcher-windows job of
# .github/workflows/release.yml and by hand on Windows:
#   powershell -File build_installer.ps1 -Version 1.2.3 -Stage out\ForzaHorizon1-ReImagined -Redist vc_redist.x64.exe -Out installer
#
# Code signing (#354) compiles twice, with -SignedUninstallerDir:
#   1. -PrepareUninstaller: Inno Setup writes the unsigned uninstaller (uninst-<Inno Setup version>-<hash>.e32) to that
#      folder and stops, as it does in its signed-uninstaller mode without a SignTool. This prints the file's path.
#   2. Once a signed copy of that file is in the folder: the real compile, which embeds its signature in
#      unins000.exe and in Setup's temporary self-copies. Setup itself is signed afterwards.
# Without -SignedUninstallerDir it is the plain unsigned build.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Redist = "",
    [string]$SignedUninstallerDir = "",
    [switch]$PrepareUninstaller,
    # ISCC.exe when Inno Setup 6 is not in Program Files or on PATH
    [string]$IsccPath = ""
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
if ($PrepareUninstaller -and -not $SignedUninstallerDir) { throw '-PrepareUninstaller needs -SignedUninstallerDir' }

$iscc = $IsccPath
foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
    if (-not $iscc -and (Test-Path -LiteralPath $candidate)) { $iscc = $candidate }
}
if (-not $iscc) {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $iscc = $found.Source }
}
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found' }

# The installer's own version resource needs a four-part number (installer.iss: VersionNumeric).
$numeric = (python (Join-Path $here 'windows_version_info.py') $Version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $numeric) { throw "Could not compute the numeric version of $Version" }

$stagePath = (Resolve-Path -LiteralPath $Stage).Path
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$outPath = (Resolve-Path -LiteralPath $Out).Path
$defines = @("/DAppVersion=$Version", "/DVersionNumeric=$numeric", "/DStage=$stagePath", "/DOut=$outPath")
if ($Redist) { $defines += "/DRedist=$((Resolve-Path -LiteralPath $Redist).Path)" }
$uninstallerDir = $null
if ($SignedUninstallerDir) {
    New-Item -ItemType Directory -Force -Path $SignedUninstallerDir | Out-Null
    $uninstallerDir = (Resolve-Path -LiteralPath $SignedUninstallerDir).Path
    $defines += "/DSignedUninstallerDir=$uninstallerDir"
}
$script = Join-Path $here 'installer.iss'

if ($PrepareUninstaller) {
    $before = @(Get-ChildItem -LiteralPath $uninstallerDir -Filter 'uninst-*.e*' -File | ForEach-Object Name)
    # This compile is meant to fail; Windows PowerShell 5.1 would turn its error output into a stop.
    $ErrorActionPreference = "Continue"
    $log = (& $iscc @defines $script 2>&1 | ForEach-Object { "$_" } | Out-String)
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $made = @(Get-ChildItem -LiteralPath $uninstallerDir -Filter 'uninst-*.e*' -File | Where-Object { $_.Extension -in '.e32', '.e64' })
    $new = @($made | Where-Object { $before -notcontains $_.Name })
    if ($code -eq 0) {
        # A compile that went through: the folder already held a signed uninstaller for this exact build.
        Write-Host $log
        throw "Inno Setup did not stop for a signature: $uninstallerDir already holds a signed uninstaller for this build"
    }
    if ($new.Count -ne 1 -or $log -notmatch 'Signed uninstaller mode is enabled') {
        Write-Host $log
        throw "Inno Setup failed before it wrote the uninstaller to sign (exit code $code; new files: $($new.Name -join ', '))"
    }
    Write-Host "The uninstaller to sign: $($new[0].FullName)"
    Write-Output $new[0].FullName
    exit 0
}

& $iscc @defines $script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit code $LASTEXITCODE)" }
