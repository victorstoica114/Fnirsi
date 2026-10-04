$ErrorActionPreference='Stop'
$uploadMsys=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'toolchain-path.txt')).Trim()
$env:MSYSTEM='UCRT64'
$env:CHERE_INVOKING='1'
& (Join-Path $uploadMsys 'usr\bin\bash.exe') -lc 'bash /tmp/dla32-project/tools/run_dla32_wch_upload_build_with_status.sh'
$uploadBuildExit=$LASTEXITCODE
Write-Output "PowerShell propagates native WCH upload build exit: $uploadBuildExit"
exit $uploadBuildExit
