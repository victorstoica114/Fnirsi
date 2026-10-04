param([int]$FirstRepeat=2,[int]$LastRepeat=10)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=Join-Path $taskRoot 'captures\stream-native-2026-10-03-r1'
$taskPy='C:\Users\User\.platformio\penv\Scripts\python.exe'
if ($FirstRepeat -lt 2 -or $LastRepeat -lt $FirstRepeat -or $LastRepeat -gt 100) { throw 'Invalid repeat range' }
function Invoke-StreamUi([string[]]$Arguments) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $taskRoot 'tools\native_dla_ui.ps1') -PidFile artifacts/native-stream-app-pid.txt @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Native UI action failed' }
}
function Wait-StreamFile([string]$Path) {
    $taskLastLength=-1L
    $taskStable=0
    for ($taskAttempt=0;$taskAttempt -lt 40;$taskAttempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-Path -LiteralPath $Path) {
            $taskLength=(Get-Item -LiteralPath $Path).Length
            if ($taskLength -gt 0 -and $taskLength -eq $taskLastLength) { $taskStable++ } else { $taskStable=0 }
            if ($taskStable -ge 4) { return }
            $taskLastLength=$taskLength
        }
    }
    throw "Native export not stable: $Path"
}
for ($taskRepeat=$FirstRepeat;$taskRepeat -le $LastRepeat;$taskRepeat++) {
    $taskStem='FNIRSI-native-Stream-all32-50MSps-th1p6-repeat'+$taskRepeat.ToString('000')
    $taskDemo=Join-Path $taskDir "$taskStem.demo"
    $taskCsv=Join-Path $taskDir "$taskStem.csv"
    $taskMarker=Join-Path $taskDir "$taskStem.complete.json"
    if ((Test-Path -LiteralPath $taskDemo) -or (Test-Path -LiteralPath $taskCsv) -or (Test-Path -LiteralPath $taskMarker)) { throw "Capture already exists: $taskStem" }
    $taskStarted=[DateTime]::UtcNow.ToString('o')
    # Observed controls in the installed full-size application; configuration stays unchanged.
    Invoke-StreamUi @('-Action','Click','-Label','Run native Stream capture','-X','109','-Y','55')
    Start-Sleep -Seconds 3
    Invoke-StreamUi @('-Action','Click','-Label','File menu','-X','209','-Y','26')
    Invoke-StreamUi @('-Action','Click','-Label','Save As native Stream session','-X','850','-Y','87')
    Invoke-StreamUi @('-Action','Keys','-Label','New Stream session path','-ExpectedDialog','Please select the save file path','-Keys',"^a$taskDemo{ENTER}")
    Wait-StreamFile $taskDemo
    $taskCheck=@'
import json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    assert z.testzip() is None
    text=z.read('set.ini').decode('utf-8-sig')
    entries={line.split('=',1)[0]:line.split('=',1)[1] for line in text.splitlines() if '=' in line}
    config=json.loads(entries['settingData']); channels=json.loads(entries['channelsSet'])
    assert config['isBuffer'] is False and config['RLE'] is False
    assert config['setHz']==50000000 and config['setTime']==100
    assert abs(config['thresholdLevel']-1.6)<1e-9 and config['triggerPosition']==50
    assert len(channels)==32 and all(c['enable'] and c['id']==i for i,c in enumerate(channels))
    assert channels[0]['triggerType']==1 and all(c['triggerType']==0 for c in channels[1:])
    assert json.loads(entries['glitchRemoval'])==[]
    assert json.loads(entries['isStopModeImmediate']) is False
    assert json.loads(entries['isOne']) is True and json.loads(entries['isInstantly']) is False
print('Native configuration and CRC verified')
'@
    $taskCheck | & $taskPy -X utf8 - $taskDemo
    if ($LASTEXITCODE -ne 0) { throw 'Saved native configuration differs; further captures refused' }
    Invoke-StreamUi @('-Action','Click','-Label','File menu','-X','209','-Y','26')
    Invoke-StreamUi @('-Action','Click','-Label','Export native waveform CSV','-X','940','-Y','87')
    Invoke-StreamUi @('-Action','Keys','-Label','New Stream CSV path','-ExpectedDialog','Please select the export file path','-Keys',"^a$taskCsv{ENTER}")
    Wait-StreamFile $taskCsv
    $taskRecord=[ordered]@{completed=$true;started_utc=$taskStarted;completed_utc=[DateTime]::UtcNow.ToString('o');csv=$taskCsv;demo=$taskDemo;csv_sha256=(Get-FileHash -LiteralPath $taskCsv -Algorithm SHA256).Hash.ToLowerInvariant();demo_sha256=(Get-FileHash -LiteralPath $taskDemo -Algorithm SHA256).Hash.ToLowerInvariant();configuration_observed=@{Fs=50000000;duration_ms=100;threshold_V=1.6;mode='Stream';isStopModeImmediate_saved=$false};note='Actual saved stop option false; no inference from QML defaults.'}
    $taskJson=$taskRecord | ConvertTo-Json -Depth 4
    $taskFile=[IO.File]::Open($taskMarker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskJson); $taskFile.Write($taskBytes,0,$taskBytes.Length) } finally { $taskFile.Dispose() }
    Write-Output "COMPLETE $taskStem"
}
