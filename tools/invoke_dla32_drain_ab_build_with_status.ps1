param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$BuildLabel)
$ErrorActionPreference='Stop'
$drainMsys=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'toolchain-path.txt')).Trim()
$env:MSYSTEM='UCRT64'
$env:CHERE_INVOKING='1'
& (Join-Path $drainMsys 'usr\bin\bash.exe') -lc "bash /tmp/dla32-project/tools/run_dla32_drain_ab_build_with_status.sh '$BuildLabel'"
$drainBuildExit=$LASTEXITCODE
Write-Output "Build wrapper preserves native Bash exit code: $drainBuildExit"
exit $drainBuildExit
