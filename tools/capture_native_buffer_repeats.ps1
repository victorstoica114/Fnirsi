param([int]$Rate=100,[double]$Threshold=0.6,[int]$FirstRepeat=1,[int]$LastRepeat=3)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=Join-Path $taskRoot 'captures\buffer-comparison-2026-10-03-r2\native'
$taskPy='C:\Users\User\.platformio\penv\Scripts\python.exe'
$taskLabel=$Threshold.ToString('0.0',[Globalization.CultureInfo]::InvariantCulture).Replace('.','p')
function Invoke-NativeUi([string[]]$Arguments) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $taskRoot 'tools\native_dla_ui.ps1') @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Native UI action failed; remaining captures refused' }
}
function Wait-NativeFile([string]$Path) {
    # Native Save As/CSV writes are asynchronous; wait for a nonempty stable file.
    $taskLastLength=-1L
    for ($taskAttempt=0;$taskAttempt -lt 30;$taskAttempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-Path -LiteralPath $Path) {
            $taskLength=(Get-Item -LiteralPath $Path).Length
            if ($taskLength -gt 0 -and $taskLength -eq $taskLastLength) { return }
            $taskLastLength=$taskLength
        }
    }
    throw "Native file not completed: $Path"
}
for ($taskRepeat=$FirstRepeat;$taskRepeat -le $LastRepeat;$taskRepeat++) {
    $taskStem="FNIRSI-native-$($Rate)MSps-th$taskLabel-repeat$taskRepeat"
    $taskDemo=Join-Path $taskDir "$taskStem.demo"
    $taskCsv=Join-Path $taskDir "$taskStem.csv"
    if ((Test-Path -LiteralPath $taskDemo) -or (Test-Path -LiteralPath $taskCsv)) { throw "Existing capture: $taskStem" }
    # Coordinates were observed in the full-size official window with File row present.
    Invoke-NativeUi @('-Action','Click','-Label','Run single Buffer capture','-X','109','-Y','143')
    Start-Sleep -Seconds 3
    Invoke-NativeUi @('-Action','Click','-Label','File menu','-X','209','-Y','26')
    Invoke-NativeUi @('-Action','Click','-Label','Save As native session','-X','850','-Y','87')
    Invoke-NativeUi @('-Action','Keys','-Label','Save As filename in workspace','-ExpectedDialog','Please select the save file path','-Keys',"^a$taskDemo{ENTER}")
    Wait-NativeFile $taskDemo
    # Validate the actual saved configuration before accepting/exporting this capture.
    $taskCheck=@'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    text=z.read('set.ini').decode('utf-8-sig')
    entries={line.split('=',1)[0]:line.split('=',1)[1] for line in text.splitlines() if '=' in line}
    config=json.loads(entries['settingData']); channels=json.loads(entries['channelsSet'])
    assert config['isBuffer'] is True and config['RLE'] is False
    assert config['setHz']==int(sys.argv[2])*1000000 and config['setTime']==10
    assert abs(config['thresholdLevel']-float(sys.argv[3]))<1e-9
    assert config['triggerPosition']==50 and channels[0]['triggerType']==1
    assert len(channels)==32 and all(c['enable'] for c in channels)
    assert json.loads(entries['glitchRemoval'])==[]
    print('Saved native configuration validated:',config['setHz'],config['thresholdLevel'])
'@
    $taskCheck | & $taskPy -X utf8 - $taskDemo "$Rate" ($Threshold.ToString('0.0',[Globalization.CultureInfo]::InvariantCulture))
    if ($LASTEXITCODE -ne 0) { throw 'Saved native configuration differs from requested case' }
    Invoke-NativeUi @('-Action','Click','-Label','File menu','-X','209','-Y','26')
    Invoke-NativeUi @('-Action','Click','-Label','Export waveform CSV','-X','940','-Y','87')
    Invoke-NativeUi @('-Action','Keys','-Label','CSV filename in workspace','-ExpectedDialog','Please select the export file path','-Keys',"^a$taskCsv{ENTER}")
    Wait-NativeFile $taskCsv
    & $taskPy -X utf8 (Join-Path $taskRoot 'tools\native_buffer_analyze.py') $taskCsv --demo $taskDemo
    if ($LASTEXITCODE -ne 0) { throw 'Native export analysis failed' }
    [pscustomobject]@{completed=$true;csv=$taskCsv;demo=$taskDemo;csv_sha256=(Get-FileHash -LiteralPath $taskCsv -Algorithm SHA256).Hash.ToLowerInvariant();demo_sha256=(Get-FileHash -LiteralPath $taskDemo -Algorithm SHA256).Hash.ToLowerInvariant()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskDir "$taskStem.complete.json") -Encoding UTF8
    Write-Output "COMPLETE $taskStem"
}
