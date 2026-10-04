param([string]$Directory='captures\buffer-comparison-2026-10-03')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=[IO.Path]::GetFullPath((Join-Path $taskRoot $Directory))
if (!(Test-Path -LiteralPath $taskDir)) { New-Item -ItemType Directory -Path $taskDir | Out-Null }
if (Get-Process -Name 'DLA-Logic','KingstVIS','pulseview' -ErrorAction SilentlyContinue) {
    throw 'Close device-owning applications before comparative CLI captures.'
}
$taskFn=Join-Path $taskRoot 'artifacts\pulseview-dla32-wiretrace'
$taskKingst=Join-Path $taskRoot 'artifacts\pulseview-la1010-reference'
$taskOriginalPath=$env:PATH
$taskOriginalFw=$env:SIGROK_FIRMWARE_DIR
$taskOriginalTrace=$env:FNIRSI_DLA32_WIRE_TRACE
$taskResults=@()
function Invoke-Capture([string]$Device,[int]$Rate,[double]$Threshold,[int]$Repeat) {
    $taskThresholdText=$Threshold.ToString('0.0',[Globalization.CultureInfo]::InvariantCulture)
    $taskStem="$Device-$($Rate)MSps-th$($taskThresholdText.Replace('.','p'))-repeat$Repeat"
    $taskOutput=Join-Path $taskDir "$taskStem.sr"
    $taskLog=Join-Path $taskDir "$taskStem.log"
    if ((Test-Path -LiteralPath $taskOutput) -or (Test-Path -LiteralPath $taskLog)) {
        throw "Refusing existing capture $taskStem"
    }
    $taskSamples=$Rate*10000 # 10 ms requested; Kingst actual count is measured later.
    $taskArguments=@('-l','4','-d')
    if ($Device -eq 'FNIRSI-Buffer') {
        $taskExe=Join-Path $taskFn 'sigrok-cli.exe'
        $env:PATH=$taskFn+';'+$taskOriginalPath
        $env:FNIRSI_DLA32_WIRE_TRACE=Join-Path $taskDir "$taskStem.wire.bin"
        $taskArguments+=@('fnirsi-dla32','-c',"samplerate=$($Rate)MHz:voltage_threshold=$taskThresholdText-$($taskThresholdText):data_source=Buffer",'--triggers','D0=r')
    } else {
        $taskExe=Join-Path $taskKingst 'sigrok-cli.exe'
        $env:PATH=$taskKingst+';'+$taskOriginalPath
        $env:SIGROK_FIRMWARE_DIR=Join-Path $taskKingst 'firmware'
        Remove-Item Env:FNIRSI_DLA32_WIRE_TRACE -ErrorAction SilentlyContinue
        $taskArguments+=@('kingst-la2016','-c',"samplerate=$($Rate)MHz:voltage_threshold=$taskThresholdText-$taskThresholdText",'--channels','CH0,CH1')
    }
    $taskArguments+=@('--samples',"$taskSamples",'-o',$taskOutput)
    $taskStart=[DateTimeOffset]::UtcNow
    # Windows PowerShell 5 treats native stderr as ErrorRecord even after
    # redirection. Keep diagnostic stderr without terminating on its text.
    $taskSavedErrorAction=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & $taskExe @taskArguments *> $taskLog
        $taskExit=$LASTEXITCODE
    } finally { $ErrorActionPreference=$taskSavedErrorAction }
    $taskResults.Add([pscustomobject]@{device=$Device;samplerate_hz=$Rate*1000000;threshold_volts=$Threshold;repeat=$Repeat;requested_samples=$taskSamples;output=$taskOutput;log=$taskLog;started_utc=$taskStart.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');exit_code=$taskExit;native_application=$false;source_frequency_hz=1000000;source_nominal_duty_percent=50;generator_commands_sent=$false})
    $taskResults | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskDir 'acquisition-manifest.json') -Encoding UTF8
    Write-Output "$taskStem exit=$taskExit"
    if ($taskExit -ne 0) { throw "Capture failed: $taskStem" }
    if ($Device -eq 'FNIRSI-Buffer') {
        if (!(Select-String -LiteralPath $taskLog -SimpleMatch 'failed 0.' -Quiet)) { throw "Trace incomplete: $taskStem" }
    }
}
$taskResults=[Collections.Generic.List[object]]::new()
try {
    for ($taskRepeat=1;$taskRepeat -le 3;$taskRepeat++) {
        # Each repetition alternates devices around the matched 2.5 V captures.
        foreach ($taskRate in @(50,100)) {
            Invoke-Capture 'Kingst' $taskRate 2.5 $taskRepeat
            Invoke-Capture 'FNIRSI-Buffer' $taskRate 2.5 $taskRepeat
        }
        Invoke-Capture 'FNIRSI-Buffer' 250 2.5 $taskRepeat
        foreach ($taskThreshold in @(1.6,0.6)) {
            foreach ($taskRate in @(50,100,250)) {
                Invoke-Capture 'FNIRSI-Buffer' $taskRate $taskThreshold $taskRepeat
            }
        }
        Invoke-Capture 'Kingst' 100 1.4 $taskRepeat
    }
} finally {
    $env:PATH=$taskOriginalPath
    if ($null -eq $taskOriginalFw) { Remove-Item Env:SIGROK_FIRMWARE_DIR -ErrorAction SilentlyContinue } else { $env:SIGROK_FIRMWARE_DIR=$taskOriginalFw }
    if ($null -eq $taskOriginalTrace) { Remove-Item Env:FNIRSI_DLA32_WIRE_TRACE -ErrorAction SilentlyContinue } else { $env:FNIRSI_DLA32_WIRE_TRACE=$taskOriginalTrace }
}
