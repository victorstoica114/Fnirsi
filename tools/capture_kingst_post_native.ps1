param([string]$Directory='captures\buffer-comparison-2026-10-03-r2\kingst-post-native')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=[IO.Path]::GetFullPath((Join-Path $taskRoot $Directory))
if (!$taskDir.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Capture path must stay in workspace' }
if (Get-Process -Name 'KingstVIS','pulseview' -ErrorAction SilentlyContinue) { throw 'Kingst-owning application running' }
if (!(Test-Path -LiteralPath $taskDir)) { New-Item -ItemType Directory -Path $taskDir | Out-Null }
$taskRuntime=Join-Path $taskRoot 'artifacts\pulseview-la1010-reference'
$taskOldPath=$env:PATH; $taskOldFw=$env:SIGROK_FIRMWARE_DIR
$taskRecords=[Collections.Generic.List[object]]::new()
try {
    $env:PATH=$taskRuntime+';'+$taskOldPath
    $env:SIGROK_FIRMWARE_DIR=Join-Path $taskRuntime 'firmware'
    for ($taskRepeat=1;$taskRepeat -le 3;$taskRepeat++) {
        foreach ($taskThreshold in @('2.5','1.4')) {
            $taskStem="Kingst-100MSps-th$($taskThreshold.Replace('.','p'))-repeat$taskRepeat"
            $taskOutput=Join-Path $taskDir "$taskStem.sr"; $taskLog=Join-Path $taskDir "$taskStem.log"
            if ((Test-Path -LiteralPath $taskOutput) -or (Test-Path -LiteralPath $taskLog)) { throw 'Refusing to overwrite capture' }
            $taskStart=[DateTimeOffset]::UtcNow
            $taskOldError=$ErrorActionPreference
            try {
                $ErrorActionPreference='Continue'
                & (Join-Path $taskRuntime 'sigrok-cli.exe') -l 4 -d kingst-la2016 -c "samplerate=100MHz:voltage_threshold=$taskThreshold-$taskThreshold" --channels CH0,CH1 --samples 1000000 -o $taskOutput *> $taskLog
                $taskExit=$LASTEXITCODE
            } finally { $ErrorActionPreference=$taskOldError }
            $taskRecords.Add([pscustomobject]@{device='Kingst';samplerate_hz=100000000;threshold_volts=[double]$taskThreshold;repeat=$taskRepeat;requested_samples=1000000;output=$taskOutput;log=$taskLog;started_utc=$taskStart.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');exit_code=$taskExit;native_application=$false;source_frequency_hz=1000000;source_nominal_duty_percent=50;generator_commands_sent=$false;context='after native FNIRSI matrix, official FNIRSI app idle, unchanged wiring'})
            $taskRecords | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskDir 'acquisition-manifest.json') -Encoding UTF8
            Write-Output "$taskStem exit=$taskExit"
            if ($taskExit -ne 0) { throw 'Kingst capture failed' }
        }
    }
} finally {
    $env:PATH=$taskOldPath
    if ($null -eq $taskOldFw) { Remove-Item Env:SIGROK_FIRMWARE_DIR -ErrorAction SilentlyContinue } else { $env:SIGROK_FIRMWARE_DIR=$taskOldFw }
}
