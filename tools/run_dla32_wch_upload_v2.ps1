param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Block,
      [ValidateRange(1,100)][int]$Repetitions=5,
      [ValidateSet('stream-only','buffer-stream')][string]$Sequence='stream-only')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=Join-Path $taskRoot 'captures\wch-upload-v2-2026-10-03-r1'
if (!(Test-Path -LiteralPath $taskDir)) { [IO.Directory]::CreateDirectory($taskDir) | Out-Null }
if (Get-Process -Name 'DLA-Logic','KingstVIS','pulseview','sigrok-cli','test_dla32_drain_ab' -ErrorAction SilentlyContinue) { throw 'Analyzer application running; refuse concurrent USB access' }
$taskPackage=Join-Path $taskRoot 'artifacts\dla32-wch-upload-v2'
$taskHarness=Join-Path $taskRoot 'artifacts\test_dla32_drain_ab.exe'
$taskDll=Join-Path $taskPackage 'libsigrok-4.dll'
if (!(Test-Path -LiteralPath $taskHarness) -or !(Test-Path -LiteralPath $taskDll)) { throw 'Diagnostic package missing' }
$taskOutput=Join-Path $taskDir $Block
$taskLog=Join-Path $taskDir "$Block.log"
$taskRecord=Join-Path $taskDir "$Block.acquisition.json"
foreach ($taskPath in @($taskOutput,$taskLog,$taskRecord)) { if (Test-Path -LiteralPath $taskPath) { throw "Refusing to overwrite $taskPath" } }
$taskExpectedBytes=[long]$Repetitions*42000000
if ($Sequence -eq 'buffer-stream') { $taskExpectedBytes*=2 }
$taskDrive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($taskDir))
if ($taskDrive.AvailableFreeSpace -lt $taskExpectedBytes+1073741824) { throw 'Insufficient space for unmodified evidence' }
$taskSha=(Get-FileHash -LiteralPath $taskDll -Algorithm SHA256).Hash.ToLowerInvariant()
$taskManifest=Get-Content -LiteralPath (Join-Path $taskPackage 'package-manifest.json') -Encoding UTF8 | ConvertFrom-Json
if ($taskManifest.variant -ne 'wch-upload-v2' -or $taskManifest.unit_failures -ne 0 -or $taskManifest.unit_checks -lt 497 -or
    $taskManifest.files.'libsigrok-4.dll'.sha256 -ne $taskSha -or $taskManifest.prearm_empty_reads -ne 1 -or
    $taskManifest.upload_diagnostic_enabled -ne $true -or $taskManifest.read_size_bytes -ne 1048576 -or
    $taskManifest.upload_pipe -ne 1 -or $taskManifest.upload_length_bytes -ne 1048576) { throw 'Package provenance or tested diagnostic configuration differs' }
$taskHarnessSha=(Get-FileHash -LiteralPath $taskHarness -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskHarnessSha -ne '65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82') { throw 'Frozen A/B harness differs' }
$taskHarnessSourceSha=(Get-FileHash -LiteralPath (Join-Path $taskRoot 'tools\test_dla32_drain_ab.c') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskHarnessSourceSha -ne $taskManifest.hardware_harness_source_sha256) { throw 'Harness source differs from packaging' }
$taskStarted=[DateTimeOffset]::UtcNow
$taskOldPath=$env:PATH
$taskCode=-1
try {
    $env:PATH=$taskPackage+';'+$taskOldPath
    $taskOldError=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & $taskHarness $taskOutput $taskSha "$Repetitions" $Sequence *> $taskLog
        $taskCode=$LASTEXITCODE
    } finally { $ErrorActionPreference=$taskOldError }
} finally { $env:PATH=$taskOldPath }
$taskData=[ordered]@{policy='wch-upload-v2';block=$Block;repetitions=$Repetitions;sequence=$Sequence;output=$taskOutput;log=$taskLog;exit_code=$taskCode;samplerate_hz=50000000;requested_samples_per_capture=5000000;threshold_volts=1.6;trigger='D0 rising';trigger_position_raw=500;physical_channel_mask='ffffffff';context='same default GLib context and same open device within block';dll_path=$taskDll;dll_sha256=$taskSha;harness_sha256=$taskHarnessSha;generator_commands_sent=$false;esp32_access=$false;started_utc=$taskStarted.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');origin_integrity_verdict='offline analysis required';throughput_benchmark=$false;upload_pipe=1;upload_length_bytes=1048576;SDK_clear_discarded_byte_count=$null;SDK_clear_does_not_prove_device_FIFO_or_frame_reset=$true}
$taskJson=$taskData | ConvertTo-Json -Depth 4
$taskFile=[IO.File]::Open($taskRecord,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskJson); $taskFile.Write($taskBytes,0,$taskBytes.Length) } finally { $taskFile.Dispose() }
Write-Output "$Block upload-queue reps=$Repetitions sequence=$Sequence exit=$taskCode"
Get-Content -LiteralPath $taskLog -Encoding UTF8 -Tail 3
if ($taskCode -ne 0) { throw 'Hardware block failed; preserve output and inspect before continuing' }
