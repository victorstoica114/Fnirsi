param(
    [ValidateSet('winusb','wch')][string]$Target,
    [Parameter(Mandatory=$true)][string]$Result
)
$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\Documente\analizor logic'
$taskHelper = Join-Path $taskRoot 'artifacts\dla32-driver-binding.exe'
$taskDevice = 'USB\VID_1A86&PID_5537\0123456789'
if ((Get-Process | Where-Object { $_.ProcessName -match '^(pulseview|DLA[-_]Logic|KingstVIS|sigrok-cli)$' })) {
    throw 'Close analyzer applications before changing its driver'
}
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'backups\driver-bindings-2026-10-06\backup-manifest.json'))) {
    throw 'Original WCH backup missing'
}
$taskNodes = @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -eq $taskDevice })
if ($taskNodes.Count -ne 1) { throw 'Exact DLA instance missing or ambiguous' }
$taskExpectedHelper = 'e5ccc85c95a4caea3ec2aac209ea998acdf1a42eda9640a2fd0ea538a9ca1ef7'
if ((Get-FileHash -LiteralPath $taskHelper -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskExpectedHelper) {
    throw 'Reviewed driver helper changed'
}
& $taskHelper --install $Target $Result
exit $LASTEXITCODE
