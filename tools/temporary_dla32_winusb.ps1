param([Parameter(Mandatory=$true)][string]$Block)
$ErrorActionPreference = 'Stop'
if ($Block -notmatch '^[A-Za-z0-9_-]{1,60}$') { throw 'Simple block required' }
$taskRoot = 'D:\Documente\analizor logic'
$taskLogs = Join-Path $taskRoot 'logs'
$taskInstaller = Join-Path $taskRoot 'tools\install_existing_dla32_driver.ps1'
$taskBind = Join-Path $taskLogs "$Block-winusb.json"
$taskRestore = Join-Path $taskLogs "$Block-wch-restore.json"
$taskDone = Join-Path $taskLogs "$Block-tests-complete.flag"
if ((Test-Path -LiteralPath $taskBind) -or (Test-Path -LiteralPath $taskRestore) -or (Test-Path -LiteralPath $taskDone)) {
    throw 'Fresh temporary binding outputs required'
}
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $taskInstaller -Target winusb -Result $taskBind
    if ($LASTEXITCODE -ne 0) { throw 'WinUSB selection failed' }
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(180)
    while ([DateTime]::UtcNow -lt $taskDeadline -and -not (Test-Path -LiteralPath $taskDone)) {
        Start-Sleep -Milliseconds 500
    }
} finally {
    # Restore the original signed WCH binding; never remove either package.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $taskInstaller -Target wch -Result $taskRestore
    if ($LASTEXITCODE -ne 0) { throw 'WCH restoration failed; inspect the result log' }
}
