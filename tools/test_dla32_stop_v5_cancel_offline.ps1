param(
    [Parameter(Mandatory=$true)][string]$RuntimeDirectory,
    [string]$WorkspaceRoot = 'D:\Documente\analizor logic',
    [string]$HarnessPath = 'D:\Documente\analizor logic\artifacts\test_dla32_stop_v5_cancel.exe'
)
$ErrorActionPreference = 'Stop'
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$workspace = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
$harness = (Resolve-Path -LiteralPath $HarnessPath).Path
$dll = Join-Path $runtime 'libsigrok-4.dll'
$expectedSha = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash.ToLowerInvariant()
$fresh = Join-Path $workspace ('cancel-offline-validation-' + [guid]::NewGuid().ToString('N'))
$outside = Join-Path ([System.IO.Path]::GetDirectoryName($workspace)) ('cancel-offline-outside-' + [guid]::NewGuid().ToString('N'))
$oldPath = $env:PATH
$oldLocation = (Get-Location).Path
$env:PATH = $runtime + ';' + $env:PATH
$checks = 0
try {
    Set-Location -LiteralPath $workspace
    & $harness --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Cancellation harness offline self-test failed.' }
    $checks++
    & $harness --validate-only $workspace $fresh $expectedSha
    if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $fresh)) { throw 'Valid SHA/fresh-path validation failed or created output.' }
    $checks++
    & $harness --validate-only $workspace $fresh ('0' * 64)
    if ($LASTEXITCODE -ne 2 -or (Test-Path -LiteralPath $fresh)) { throw 'Wrong DLL SHA was not rejected without output.' }
    $checks++
    & $harness --validate-only $workspace $workspace $expectedSha
    if ($LASTEXITCODE -ne 2) { throw 'Existing output directory was not rejected.' }
    $checks++
    & $harness --validate-only $workspace $outside $expectedSha
    if ($LASTEXITCODE -ne 2 -or (Test-Path -LiteralPath $outside)) { throw 'Output outside resolved workspace was not rejected.' }
    $checks++
    & $harness --validate-only $workspace 'relative-output' $expectedSha
    if ($LASTEXITCODE -ne 2 -or (Test-Path -LiteralPath 'relative-output')) { throw 'Relative output was not rejected without output.' }
    $checks++
    & $harness --validate-only $workspace $fresh 'wrong'
    if ($LASTEXITCODE -ne 2 -or (Test-Path -LiteralPath $fresh)) { throw 'Malformed SHA was not rejected without output.' }
    $checks++
    & $harness
    if ($LASTEXITCODE -ne 2) { throw 'Missing CLI arguments were not rejected.' }
    $checks++
    Write-Output ('PASS offline entrypoint checks: {0}; DLL SHA {1}; EXE SHA {2}; no sr_init/scan/open or hardware.' -f $checks, $expectedSha, (Get-FileHash -LiteralPath $harness -Algorithm SHA256).Hash.ToLowerInvariant())
}
finally {
    Set-Location -LiteralPath $oldLocation
    $env:PATH = $oldPath
}
