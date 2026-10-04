<# Root-owned hardware wrapper. Preparation/review agents must not execute it. #>
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskBase=Join-Path $taskRoot 'captures\wch-stop-v5-sdk0-2026-10-04-r1'
$taskOutput=Join-Path $taskBase 'D-cancel-1'
$taskLog=Join-Path $taskBase 'D-cancel-1.log'
$taskRecord=Join-Path $taskBase 'D-cancel-1.acquisition.json'
$taskPackage=Join-Path $taskRoot 'artifacts\dla32-wch-stop-v5-sdk0'
$taskManifestPath=Join-Path $taskPackage 'package-manifest.json'
$taskHarness=Join-Path $taskRoot 'artifacts\test_dla32_stop_v5_cancel.exe'
$taskHarnessSource=Join-Path $taskRoot 'tools\test_dla32_stop_v5_cancel.c'
$taskValidationPath=Join-Path $taskRoot 'artifacts\test_dla32_stop_v5_cancel.validation.json'
$taskDll=Join-Path $taskPackage 'libsigrok-4.dll'
$taskExpectedDllSha='e215956d4ac915a0f6f24ce4d5536072d0ff19f0788d54bbf68854474f7211ed'
$taskExpectedHarnessSha='6ad73ed94261ed8f3f8d2b10d199f54ab3e425561f88046ca91baa24f3816763'
$taskExpectedSourceSha='ac5e0995c9a111a1df97c172ac67563543e784e20f9e8fd4f9427796286fc216'
$taskExpectedValidationSha='9965430a3f91483ced352e993d77c963c0c7ffa999f029d5d4d9ab2a8220e7ed'
$taskExpectedManifestSha='4d8f3f54e4380a5b2f96f0404dac698ca4823d9c2caf04f126af2d25949466cd'
foreach ($taskPath in @($taskOutput,$taskLog,$taskRecord)) {
    if (Test-Path -LiteralPath $taskPath) { throw "Refusing to overwrite $taskPath" }
}
if (Get-Process -Name 'DLA-Logic','KingstVIS','pulseview','sigrok-cli','test_dla32_drain_ab','test_dla32_stop_v5_cancel' -ErrorAction SilentlyContinue) { throw 'Analyzer application running; refuse concurrent USB access' }
function Assert-TaskSha([string]$Path,[string]$Expected) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected) { throw "Frozen artifact differs: $Path" }
}
Assert-TaskSha $taskManifestPath $taskExpectedManifestSha
Assert-TaskSha $taskDll $taskExpectedDllSha
Assert-TaskSha $taskHarness $taskExpectedHarnessSha
Assert-TaskSha $taskHarnessSource $taskExpectedSourceSha
Assert-TaskSha $taskValidationPath $taskExpectedValidationSha
$taskManifest=Get-Content -LiteralPath $taskManifestPath -Encoding UTF8 | ConvertFrom-Json
if ($taskManifest.variant -ne 'wch-stop-v5-sdk0' -or $taskManifest.sdk_policy -ne 0 -or
    $taskManifest.unit_checks -ne 551 -or $taskManifest.sdk0_explicit_macro_unit_checks -ne 551 -or $taskManifest.unit_failures -ne 0 -or
    $taskManifest.timeout_helper_checks -ne 47 -or $taskManifest.make_check_cases -ne 92 -or $taskManifest.native_bash_exit_code -ne 0 -or
    $taskManifest.upload_diagnostic_enabled -ne $false -or $taskManifest.sdk_upload_exports_required -ne $false -or
    $taskManifest.default_upload_macro_omitted -ne $true -or $taskManifest.sdk0_pending_stop_retention -ne $true -or
    $taskManifest.pending_hardware_stop_survives_application_end -ne $true -or $taskManifest.wch_configuration_pwm_blocked_while_transport_ownership_pending -ne $true -or
    $taskManifest.transport_header_unchanged_vs_v4 -ne $true -or $taskManifest.protocol_decoder_unchanged_vs_v4 -ne $true -or
    $taskManifest.api_sha256 -ne '6a96abf23315a379f24f2a949fb7fc4b9b5be990bbd994ceed7ba5d2e8bb4fe8' -or
    $taskManifest.read_size_bytes -ne 1048576 -or $taskManifest.prearm_empty_reads -ne 1 -or
    $taskManifest.command_write_timeout_ms -ne 1000 -or $taskManifest.sample_read_timeout_ms -ne 1000 -or $taskManifest.drain_read_timeout_ms -ne 20) { throw 'Tested V5 SDK0 configuration differs' }
foreach ($taskEntry in $taskManifest.files.PSObject.Properties) {
    Assert-TaskSha (Join-Path $taskPackage $taskEntry.Name) $taskEntry.Value.sha256
}
$taskSource=Join-Path $taskRoot 'artifacts\dla32-wch-stop-v5-source\libsigrok'
if ($taskManifest.source_directory -ne $taskSource) { throw 'Source path differs' }
$taskSourceHashes=Get-Content -LiteralPath (Join-Path $taskPackage 'source-sha256.json') -Encoding UTF8 | ConvertFrom-Json
foreach ($taskEntry in $taskSourceHashes.PSObject.Properties) {
    Assert-TaskSha (Join-Path $taskSource $taskEntry.Name) $taskEntry.Value
}
$taskValidation=Get-Content -LiteralPath $taskValidationPath -Encoding UTF8 | ConvertFrom-Json
if ($taskValidation.compiled_self_test_checks -ne 42 -or $taskValidation.compiled_self_test_failures -ne 0 -or
    $taskValidation.CLI_SHA_fresh_path_entrypoint_checks -ne 8 -or $taskValidation.CLI_SHA_fresh_path_entrypoint_failures -ne 0 -or
    $taskValidation.offline_native_exit_code -ne 0 -or $taskValidation.hardware_access -ne $false -or
    $taskValidation.offline_runtime_DLL_sha256 -ne $taskExpectedDllSha) { throw 'Cancellation harness offline validation differs' }
foreach ($taskEntry in $taskValidation.files.PSObject.Properties) {
    Assert-TaskSha (Join-Path $taskRoot $taskEntry.Name) $taskEntry.Value
}
# The DLL identity/ABI is pinned; reject a different SDK selected by environment.
$taskSdkPath=if ($env:FNIRSI_WCH_DLL) { $env:FNIRSI_WCH_DLL } else { Join-Path $(if ($env:ProgramW6432) { $env:ProgramW6432 } else { 'C:\Program Files' }) 'FNIRSI\DLA Logic\CH375DLL64.dll' }
Assert-TaskSha $taskSdkPath $taskManifest.vendor_sdk_sha256
$taskDrive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($taskRoot))
if ($taskDrive.AvailableFreeSpace -lt 167772160+1073741824) { throw 'Insufficient space for complete cancellation/recovery evidence' }
if (!(Test-Path -LiteralPath $taskBase)) { [IO.Directory]::CreateDirectory($taskBase) | Out-Null }
# The C harness resolves workspace/output-parent through Windows handles and
# creates only the missing final directory; no outputs are overwritten.
$taskStarted=[DateTimeOffset]::UtcNow
$taskOldPath=$env:PATH
$taskOldLocation=(Get-Location).Path
$taskCode=-1
$taskLaunchError=$null
$taskLogFile=[IO.File]::Open($taskLog,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
$taskLogWriter=[IO.StreamWriter]::new($taskLogFile,[Text.UTF8Encoding]::new($false))
$taskLogWriter.AutoFlush=$true
try {
    Set-Location -LiteralPath $taskRoot
    $env:PATH=$taskPackage+';'+$taskOldPath
    $taskOldError=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & $taskHarness $taskRoot $taskOutput $taskExpectedDllSha 2>&1 | ForEach-Object { $taskLogWriter.WriteLine($_.ToString()) }
        $taskCode=$LASTEXITCODE
    } catch { $taskLaunchError=$_.Exception.Message; $taskCode=-1 }
    finally { $ErrorActionPreference=$taskOldError }
} finally { $taskLogWriter.Dispose(); $env:PATH=$taskOldPath; Set-Location -LiteralPath $taskOldLocation }
$taskData=[ordered]@{policy='wch-stop-v5-sdk0';block='D-cancel-1';capture_kind='timer_cancel_then_same_open_device_finite_Buffer_Stream';output=$taskOutput;log=$taskLog;exit_code=$taskCode;launch_error=$taskLaunchError;cancel_samplerate_hz=1000000;cancel_configured_sample_limit=10000000;cancel_nominal_timer_ms=100;cancel_sample_count_criterion='0 <= actual_samples < 10000000; exactly one timer stop; no LOGIC after stop';recovery_samplerate_hz=50000000;recovery_samples_per_capture=5000000;recovery_capture_count=2;threshold_volts=1.6;trigger='D0 rising';trigger_position_raw=500;physical_channel_mask='ffffffff';context='single default GLib context; same open sdi for allthree stages';dll_path=$taskDll;dll_sha256=$taskExpectedDllSha;package_manifest_sha256=$taskExpectedManifestSha;harness_sha256=$taskExpectedHarnessSha;harness_source_sha256=$taskExpectedSourceSha;harness_validation_sha256=$taskExpectedValidationSha;generator_commands_sent=$false;esp32_access=$false;started_utc=$taskStarted.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');timer_deadline_is_not_hardware_timing_guarantee=$true;physical_integrity_verdict='offline analysis required';SDK_upload_enabled=$false;accepted_STOP_markers_required=$true;outer_exit0_required=$true}
$taskJson=$taskData | ConvertTo-Json -Depth 4
$taskFile=[IO.File]::Open($taskRecord,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskJson); $taskFile.Write($taskBytes,0,$taskBytes.Length) } finally { $taskFile.Dispose() }
Write-Output "D-cancel-1 V5 SDK0 cancellation/recovery exit=$taskCode"
if (Test-Path -LiteralPath $taskLog) { Get-Content -LiteralPath $taskLog -Encoding UTF8 -Tail 4 }
if ($taskCode -ne 0) { throw 'Cancellation/recovery outer execution failed; preserve evidence and inspect before continuing' }
$taskSequence=Get-Content -LiteralPath (Join-Path $taskOutput 'sequence-result.json') -Encoding UTF8 | ConvertFrom-Json
if ($taskSequence.completed_capture_stages -ne 3 -or $taskSequence.outer_lifecycle_and_count_pass -ne $true -or
    $taskSequence.device_close_attempted -ne $true -or $taskSequence.device_close_status -ne 0 -or
    $taskSequence.sr_exit_attempted -ne $true -or $taskSequence.sr_exit_status -ne 0 -or
    $taskSequence.expected_loaded_DLL_SHA256 -ne $taskExpectedDllSha) { throw 'Cancellation/recovery sequence result failed; preserve evidence for offline analysis' }
