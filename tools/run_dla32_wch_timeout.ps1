param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Block,
      [ValidateRange(1,100)][int]$Repetitions=5,
      [ValidateSet('stream-only','buffer-stream')][string]$Sequence='stream-only',
      [string]$Directory='captures\wch-timeout-2026-10-04-r1')
$ErrorActionPreference='Stop'
$Policy='wch-timeout'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=[IO.Path]::GetFullPath((Join-Path $taskRoot $Directory))
if (!$taskDir.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Workspace directory required' }
[IO.Directory]::CreateDirectory($taskDir) | Out-Null
if (Get-Process -Name 'DLA-Logic','KingstVIS','pulseview','sigrok-cli','test_dla32_drain_ab' -ErrorAction SilentlyContinue) { throw 'Analyzer application running; refuse concurrent USB access' }
$taskPackage=Join-Path $taskRoot 'artifacts\dla32-wch-timeout'
$taskHarness=Join-Path $taskRoot 'artifacts\test_dla32_drain_ab.exe'
$taskDll=Join-Path $taskPackage 'libsigrok-4.dll'
if (!(Test-Path -LiteralPath $taskHarness) -or !(Test-Path -LiteralPath $taskDll)) { throw 'Diagnostic build or harness missing' }
$taskOutput=Join-Path $taskDir $Block
$taskLog=Join-Path $taskDir "$Block.log"
$taskRecord=Join-Path $taskDir "$Block.acquisition.json"
foreach ($taskPath in @($taskOutput,$taskLog,$taskRecord)) {
    if (Test-Path -LiteralPath $taskPath) { throw "Refusing to overwrite $taskPath" }
}
$taskExpectedBytes=[long]$Repetitions*40000000
if ($Sequence -eq 'buffer-stream') { $taskExpectedBytes*=2 }
$taskDrive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($taskDir))
if ($taskDrive.AvailableFreeSpace -lt $taskExpectedBytes+1073741824) { throw 'Insufficient space for unchanged RAW and LOGIC evidence' }
$taskSha=(Get-FileHash -LiteralPath $taskDll -Algorithm SHA256).Hash.ToLowerInvariant()
$taskManifest=Get-Content -LiteralPath (Join-Path $taskPackage 'package-manifest.json') -Encoding UTF8 | ConvertFrom-Json
if ($taskManifest.variant -ne $Policy -or $taskManifest.unit_failures -ne 0 -or $taskManifest.unit_checks -lt 443 -or
    $taskManifest.timeout_mock_checks -lt 47 -or $taskManifest.timeout_mock_failures -ne 0 -or
    $taskManifest.make_check_cases -ne 92 -or $taskManifest.make_check_failures -ne 0 -or
    $taskManifest.upload_diagnostic_enabled -ne $false -or $taskManifest.command_write_timeout_ms -ne 1000 -or
    $taskManifest.read_size_bytes -ne 1048576 -or
    $taskManifest.files.'libsigrok-4.dll'.sha256 -ne $taskSha) { throw 'Diagnostic package provenance or unit checks differ' }
$taskPolicyReads=1
if ($taskManifest.prearm_empty_reads -ne $taskPolicyReads) { throw 'Wrong pre-arm policy in package manifest' }
$taskHarnessSourceSha=(Get-FileHash -LiteralPath (Join-Path $taskRoot 'tools\test_dla32_drain_ab.c') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskHarnessSourceSha -ne $taskManifest.hardware_harness_source_sha256) { throw 'Harness source changed since packaging' }
$taskStarted=[DateTimeOffset]::UtcNow
$taskOldPath=$env:PATH; $taskCode=-1
try {
    $env:PATH=$taskPackage+';'+$taskOldPath
    $taskOldError=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & $taskHarness $taskOutput $taskSha "$Repetitions" $Sequence *> $taskLog
        $taskCode=$LASTEXITCODE
    } finally { $ErrorActionPreference=$taskOldError }
} finally { $env:PATH=$taskOldPath }
$taskData=[ordered]@{policy=$Policy;block=$Block;repetitions=$Repetitions;sequence=$Sequence;output=$taskOutput;log=$taskLog;exit_code=$taskCode;samplerate_hz=50000000;requested_samples_per_capture=5000000;threshold_volts=1.6;trigger='D0 rising';trigger_position_raw=500;physical_channel_mask='ffffffff';context='same default GLib context and same open device within block';dll_path=$taskDll;dll_sha256=$taskSha;harness_sha256=(Get-FileHash -LiteralPath $taskHarness -Algorithm SHA256).Hash.ToLowerInvariant();generator_commands_sent=$false;esp32_access=$false;upload_diagnostic_enabled=$false;operation_timeout_policy='all fields requested read_ms before ReadEndP; all fields 1000 before WriteData';started_utc=$taskStarted.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');origin_integrity_verdict='offline analysis required';throughput_benchmark=$false}
$taskData | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $taskRecord -Encoding UTF8
Write-Output "$Block policy=$Policy reps=$Repetitions sequence=$Sequence exit=$taskCode"
Get-Content -LiteralPath $taskLog -Encoding UTF8 -Tail 3
if ($taskCode -ne 0) { throw 'Diagnostic block failed; preserve output and inspect before further acquisition' }
