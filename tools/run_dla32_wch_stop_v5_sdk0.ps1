param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Block,
      [ValidateRange(1,100)][int]$Repetitions=5,
      [ValidateSet('stream-only','buffer-stream')][string]$Sequence='stream-only')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDir=Join-Path $taskRoot 'captures\wch-stop-v5-sdk0-2026-10-04-r1'
if (!(Test-Path -LiteralPath $taskDir)) { [IO.Directory]::CreateDirectory($taskDir) | Out-Null }
if (Get-Process -Name 'DLA-Logic','KingstVIS','pulseview','sigrok-cli','test_dla32_drain_ab','test_dla32_stop_v5_cancel' -ErrorAction SilentlyContinue) { throw 'Analyzer application running; refuse concurrent USB access' }
$taskPackage=Join-Path $taskRoot 'artifacts\dla32-wch-stop-v5-sdk0'
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
if ($taskManifest.variant -ne 'wch-stop-v5-sdk0' -or $taskManifest.sdk_policy -ne 0 -or
    $taskManifest.unit_failures -ne 0 -or $taskManifest.unit_checks -lt 551 -or
    $taskManifest.inherited_sdk0_a07_source_checks -ne 443 -or $taskManifest.sdk0_explicit_macro_unit_checks -ne $taskManifest.unit_checks -or
    $taskManifest.timeout_helper_checks -ne 47 -or $taskManifest.timeout_helper_failures -ne 0 -or
    $taskManifest.make_check_cases -ne 92 -or $taskManifest.make_check_failures -ne 0 -or $taskManifest.make_check_errors -ne 0 -or
    $taskManifest.native_bash_exit_code -ne 0 -or $taskManifest.compile_runtime_guards -ne 7 -or
    $taskManifest.files.'libsigrok-4.dll'.sha256 -ne $taskSha -or $taskManifest.prearm_empty_reads -ne 1 -or
    $taskManifest.upload_diagnostic_enabled -ne $false -or $taskManifest.sdk_upload_exports_required -ne $false -or
    $taskManifest.default_upload_macro_omitted -ne $true -or $taskManifest.sdk_disable_precedes_hardware_stop -ne $false -or
    $taskManifest.read_size_bytes -ne 1048576 -or $null -ne $taskManifest.upload_pipe -or $null -ne $taskManifest.upload_length_bytes -or
    $taskManifest.sdk0_pending_stop_retention -ne $true -or $taskManifest.pending_hardware_stop_survives_application_end -ne $true -or
    $taskManifest.wch_configuration_pwm_blocked_while_transport_ownership_pending -ne $true -or
    $taskManifest.api_sha256 -ne '6a96abf23315a379f24f2a949fb7fc4b9b5be990bbd994ceed7ba5d2e8bb4fe8' -or
    $taskManifest.transport_header_sha256 -ne 'c45ce7d4c48b5892b267d3a2b01e1b61addf95a6d581d9253596ddb86da4fa85' -or
    $taskManifest.transport_header_unchanged_vs_v4 -ne $true -or $taskManifest.protocol_decoder_unchanged_vs_v4 -ne $true -or
    $taskManifest.per_operation_timeout_setter_required -ne $true -or $taskManifest.failed_timeout_setter_suppresses_io -ne $true -or
    $taskManifest.command_write_timeout_ms -ne 1000 -or $taskManifest.sample_read_timeout_ms -ne 1000 -or $taskManifest.drain_read_timeout_ms -ne 20 -or
    $taskManifest.sdk1_success_markers_and_order_preserved -ne $true -or $taskManifest.libusb_stop_cancel_behavior_unchanged -ne $true) { throw 'Package provenance or tested V5 SDK0 configuration differs' }
foreach ($taskEntry in $taskManifest.files.PSObject.Properties) {
    $taskFile=Join-Path $taskPackage $taskEntry.Name
    if ((Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskEntry.Value.sha256) { throw "Package file differs: $($taskEntry.Name)" }
}
$taskSource=Join-Path $taskRoot 'artifacts\dla32-wch-stop-v5-source\libsigrok'
if ($taskManifest.source_directory -ne $taskSource) { throw 'Source directory differs' }
$taskSourceHashes=Get-Content -LiteralPath (Join-Path $taskPackage 'source-sha256.json') -Encoding UTF8 | ConvertFrom-Json
foreach ($taskEntry in $taskSourceHashes.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $taskSource $taskEntry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskEntry.Value) { throw "Frozen V5 source differs: $($taskEntry.Name)" }
}
$taskHarnessSha=(Get-FileHash -LiteralPath $taskHarness -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskHarnessSha -ne '65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82' -or $taskHarnessSha -ne $taskManifest.hardware_harness_sha256) { throw 'Frozen A/B harness differs' }
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
$taskData=[ordered]@{policy='wch-stop-v5-sdk0';block=$Block;repetitions=$Repetitions;sequence=$Sequence;output=$taskOutput;log=$taskLog;exit_code=$taskCode;samplerate_hz=50000000;requested_samples_per_capture=5000000;threshold_volts=1.6;trigger='D0 rising';trigger_position_raw=500;physical_channel_mask='ffffffff';context='same default GLib context and same open device within block';dll_path=$taskDll;dll_sha256=$taskSha;harness_sha256=$taskHarnessSha;harness_source_sha256=$taskHarnessSourceSha;generator_commands_sent=$false;esp32_access=$false;started_utc=$taskStarted.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');origin_integrity_verdict='offline analysis required';throughput_benchmark=$false;sdk_upload_enabled=$false;SDK_disable_precedes_STOP=$false;accepted_STOP_marker_required=$true;pendingSTOP_survivesEND=$true;command_write_timeout_ms=1000;sample_read_timeout_ms=1000;drain_read_timeout_ms=20;per_operation_timeout_fields_uniform=$true}
$taskJson=$taskData | ConvertTo-Json -Depth 4
$taskFile=[IO.File]::Open($taskRecord,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskJson); $taskFile.Write($taskBytes,0,$taskBytes.Length) } finally { $taskFile.Dispose() }
Write-Output "$Block V5 SDK0 reps=$Repetitions sequence=$Sequence exit=$taskCode"
Get-Content -LiteralPath $taskLog -Encoding UTF8 -Tail 3
if ($taskCode -ne 0) { throw 'Hardware block failed; preserve output and inspect before continuing' }
