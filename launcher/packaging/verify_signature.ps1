# Checks the Authenticode signatures of signed Windows files (#354). Used by .github/workflows/release.yml
# after each signing request and by test_installer.ps1 on the installed files; by hand:
#   powershell -File verify_signature.ps1 -Mode release ForzaHorizon1-ReImagined-Setup-1.2.3.exe forza-launcher.exe
#
# -Mode release: the signature is valid (it chains to a root Windows trusts), its signer's subject
#   matches -Publisher, and it carries a timestamp, so it stays valid after the certificate expires.
# -Mode test: SignPath's test-signing policy, whose certificate Windows does not trust. The file must
#   still be signed, unmodified since (no hash mismatch) and timestamped.
# Files without an .exe or .dll extension (Inno Setup's uninst-*.e32) are checked through a copy named .exe.
# The files are the only positional arguments (without PositionalBinding = $false, the first file would
# bind to -Publisher).
[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('release', 'test')][string]$Mode = 'release',
    [string]$Publisher = 'SignPath Foundation',
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)][object[]]$Path
)
$ErrorActionPreference = "Stop"
# Several files as separate arguments, or one array of them
$files = @($Path | ForEach-Object { $_ } | ForEach-Object { "$_" })
$failed = @()
foreach ($item in $files) {
    $file = (Resolve-Path -LiteralPath $item).Path
    $checked = $file
    $copy = $null
    if ([IO.Path]::GetExtension($file) -notin '.exe', '.dll') {
        $copy = Join-Path ([IO.Path]::GetTempPath()) ("verify-signature-" + [guid]::NewGuid().ToString('N') + ".exe")
        Copy-Item -LiteralPath $file -Destination $copy
        $checked = $copy
    }
    try {
        $sig = Get-AuthenticodeSignature -LiteralPath $checked
    } finally {
        if ($copy) { Remove-Item -LiteralPath $copy -Force -ErrorAction SilentlyContinue }
    }
    $subject = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { '' }
    $stamp = if ($sig.TimeStamperCertificate) { $sig.TimeStamperCertificate.Subject } else { '' }
    Write-Host "$([IO.Path]::GetFileName($file)): $($sig.Status) | signer: $subject | timestamp: $stamp"
    $problems = @()
    if (-not $sig.SignerCertificate -or $sig.Status -in 'NotSigned', 'HashMismatch', 'NotSupportedFileFormat', 'Incompatible') {
        $problems += "not signed, or changed after signing ($($sig.Status): $($sig.StatusMessage))"
    } elseif ($Mode -eq 'release') {
        if ($sig.Status -ne 'Valid') { $problems += "signature not valid ($($sig.Status): $($sig.StatusMessage))" }
        if ($subject -notmatch "(^|, )(CN|O)=`"?$([regex]::Escape($Publisher))") { $problems += "signed by '$subject', not '$Publisher'" }
    }
    if ($sig.SignerCertificate -and -not $sig.TimeStamperCertificate) { $problems += 'no timestamp' }
    if ($problems) {
        Write-Host "  FAIL: $($problems -join '; ')"
        $failed += $file
    }
}
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) file(s) failed the $Mode signature check"
    exit 1
}
Write-Host "all $($files.Count) file(s) pass the $Mode signature check"
exit 0
