param([Parameter(Mandatory=$true)][string]$Directory,
      [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Stem)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=[IO.Path]::GetFullPath((Join-Path $taskRoot $Directory))
if (!$taskDir.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Capture must remain in workspace' }
if (!(Test-Path -LiteralPath $taskDir)) { throw 'Output directory must already exist' }
if (Get-Process -Name 'KingstVIS','pulseview','sigrok-cli' -ErrorAction SilentlyContinue) { throw 'Analyzer application is running' }
$taskCapture=Join-Path $taskDir "$Stem.sr"
$taskLog=Join-Path $taskDir "$Stem.log"
$taskRecord=Join-Path $taskDir "$Stem.acquisition.json"
foreach ($taskPath in @($taskCapture,$taskLog,$taskRecord)) {
    if (Test-Path -LiteralPath $taskPath) { throw "Refusing to overwrite $taskPath" }
}
$taskRuntime=Join-Path $taskRoot 'artifacts\pulseview-la1010-reference'
$taskOldPath=$env:PATH; $taskOldFw=$env:SIGROK_FIRMWARE_DIR
$taskStarted=[DateTimeOffset]::UtcNow
$taskCode=-1
try {
    $env:PATH=$taskRuntime+';'+$taskOldPath
    $env:SIGROK_FIRMWARE_DIR=Join-Path $taskRuntime 'firmware'
    $taskOldError=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & (Join-Path $taskRuntime 'sigrok-cli.exe') -l 4 -d kingst-la2016 -c 'samplerate=100MHz:voltage_threshold=2.5-2.5' --channels CH0,CH1 --samples 1000000 -o $taskCapture *> $taskLog
        $taskCode=$LASTEXITCODE
    } finally { $ErrorActionPreference=$taskOldError }
} finally {
    $env:PATH=$taskOldPath
    if ($null -eq $taskOldFw) { Remove-Item Env:SIGROK_FIRMWARE_DIR -ErrorAction SilentlyContinue } else { $env:SIGROK_FIRMWARE_DIR=$taskOldFw }
}
$taskData=[ordered]@{device='Kingst LA1010';capture=$taskCapture;log=$taskLog;exit_code=$taskCode;requested_samples=1000000;samplerate_hz=100000000;threshold_volts=2.5;channels=@('CH0','CH1');source_nodes=@('ESP32_GPIO32','FNIRSI_PWM0');nominal_source_frequency_hz=1000000;nominal_source_duty_percent=50;generator_commands_sent=$false;esp32_access=$false;started_utc=$taskStarted.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');dll_sha256=(Get-FileHash -LiteralPath (Join-Path $taskRuntime 'libsigrok-4.dll') -Algorithm SHA256).Hash.ToLowerInvariant();note='Actual saved count and durations require offline analysis; no exact requested-count or calibrated timing claim.'}
$taskData | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $taskRecord -Encoding UTF8
Write-Output "$Stem Kingst exit=$taskCode"
if ($taskCode -ne 0) { throw 'Kingst capture failed' }
