$ErrorActionPreference = 'Stop'
$taskProject = Split-Path $PSScriptRoot -Parent
$taskDriver = Join-Path $taskProject 'downloads\kingst-la1010\windows-driver\Kingst.inf'
$taskCatalog = Join-Path $taskProject 'downloads\kingst-la1010\windows-driver\kingstx64.cat'
$taskLog = Join-Path $taskProject 'logs\install-la1010-driver.log'
$taskResult = Join-Path $taskProject 'logs\install-la1010-driver-result.json'

try {
    $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new($taskIdentity)
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows administrator elevation is required to install this USB driver.'
    }
    $taskText = Get-Content -LiteralPath $taskDriver -Raw
    if ($taskText -notmatch 'FW01A2Install, USB\\VID_77A1&PID_01A2' -or
        $taskText -notmatch 'ServiceBinary\s*=\s*%12%\\WinUSB.sys') {
        throw 'Expected original Kingst VID77A1/PID01A2 WinUSB package is missing.'
    }
    $taskSignature = Get-AuthenticodeSignature -FilePath $taskCatalog
    if ($taskSignature.Status -ne 'Valid') {
        throw 'Kingst catalog signature is not valid.'
    }
    $taskDevices = @(Get-PnpDevice -PresentOnly | Where-Object {
        $_.InstanceId -like 'USB\VID_77A1&PID_01A2\*'
    })
    if ($taskDevices.Count -ne 1) {
        throw 'Exactly one connected Kingst VID77A1/PID01A2 device is required.'
    }
    & (Join-Path $env:SystemRoot 'System32\pnputil.exe') /add-driver $taskDriver /install 2>&1 |
        Tee-Object -FilePath $taskLog
    $taskExit = $LASTEXITCODE
    [ordered]@{
        pnputil_exit_code = $taskExit
        inf = $taskDriver
        inf_sha256 = (Get-FileHash -LiteralPath $taskDriver -Algorithm SHA256).Hash
        catalog_signature = [string]$taskSignature.Status
        device_instance = $taskDevices[0].InstanceId
        source = 'Official KingstVIS 3.6.6 Windows package; unmodified extracted driver'
    } | ConvertTo-Json | Set-Content -LiteralPath $taskResult -Encoding utf8
    exit $taskExit
} catch {
    $_ | Out-File -LiteralPath $taskLog -Encoding utf8 -Append
    [ordered]@{ error = $_.Exception.Message } | ConvertTo-Json |
        Set-Content -LiteralPath $taskResult -Encoding utf8
    exit 1
}
