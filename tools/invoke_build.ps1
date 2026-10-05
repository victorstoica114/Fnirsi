param([ValidateSet('all','decode','sigrok','cli','pulseview')][string]$Component='all')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$taskMsys=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'toolchain-path.txt')).Trim()
if (-not (Test-Path -LiteralPath "$taskMsys\usr\bin\bash.exe")) {
    throw 'The build environment is missing; check tools/toolchain-path.txt.'
}
$env:MSYSTEM='UCRT64'
$env:CHERE_INVOKING='1'
# A shell script avoids nested PowerShell quoting of paths containing spaces.
& "$taskMsys\usr\bin\bash.exe" -lc "bash /tmp/dla32-project/tools/build_windows.sh $Component"
if ($LASTEXITCODE -ne 0) { throw "Build $Component failed: $LASTEXITCODE" }
