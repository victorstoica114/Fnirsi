<# Root-owned hardware wrapper. Preparation/review agents must not execute it. #>
param([switch]$OfflineValidateOnly)
$ErrorActionPreference='Stop'
function ConvertTo-TaskWindowsArgument([string]$Value) {
    $taskBuilder=[Text.StringBuilder]::new()
    [void]$taskBuilder.Append('"')
    $taskSlashes=0
    foreach ($taskChar in $Value.ToCharArray()) {
        if ($taskChar -eq '\') { $taskSlashes++; continue }
        if ($taskChar -eq '"') {
            [void]$taskBuilder.Append(('\' * (2*$taskSlashes+1)))
            [void]$taskBuilder.Append('"')
        } else {
            [void]$taskBuilder.Append(('\' * $taskSlashes))
            [void]$taskBuilder.Append($taskChar)
        }
        $taskSlashes=0
    }
    [void]$taskBuilder.Append(('\' * (2*$taskSlashes)))
    [void]$taskBuilder.Append('"')
    return $taskBuilder.ToString()
}
function Invoke-TaskBoundedProcess([string]$FilePath,[string[]]$ArgumentList,[string]$LogPath,[int]$TimeoutMilliseconds) {
    $taskResult=[ordered]@{exit_code=-1;native_exit_code=$null;timed_out=$false;watchdog_milliseconds=$TimeoutMilliseconds;process_id=$null;process_exit_observed=$false;kill_attempted=$false;kill_succeeded=$false;output_drained=$false;launch_error=$null;process_error=$null;elapsed_ms=0;stdout='';stderr=''}
    $taskProcess=[Diagnostics.Process]::new()
    $taskProcess.StartInfo.FileName=$FilePath
    $taskProcess.StartInfo.Arguments=(($ArgumentList | ForEach-Object { ConvertTo-TaskWindowsArgument $_ }) -join ' ')
    $taskProcess.StartInfo.WorkingDirectory=$taskRoot
    $taskProcess.StartInfo.UseShellExecute=$false
    $taskProcess.StartInfo.CreateNoWindow=$true
    $taskProcess.StartInfo.RedirectStandardOutput=$true
    $taskProcess.StartInfo.RedirectStandardError=$true
    $taskProcess.StartInfo.EnvironmentVariables['PATH']=$taskPackage+';'+$env:PATH
    $taskLogFile=[IO.File]::Open($LogPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $taskWriter=[IO.StreamWriter]::new($taskLogFile,[Text.UTF8Encoding]::new($false))
    $taskWriter.AutoFlush=$true
    $taskClock=[Diagnostics.Stopwatch]::StartNew()
    $taskStartedProcess=$false
    try {
        $taskStartedProcess=$taskProcess.Start()
        if (!$taskStartedProcess) { throw 'Process.Start returned false' }
        $taskResult.process_id=$taskProcess.Id
        # Both pipes drain asynchronously while the independent process wait runs.
        $taskStdout=$taskProcess.StandardOutput.ReadToEndAsync()
        $taskStderr=$taskProcess.StandardError.ReadToEndAsync()
        if (!$taskProcess.WaitForExit($TimeoutMilliseconds)) {
            $taskResult.timed_out=$true
            $taskResult.exit_code=124
            $taskResult.kill_attempted=$true
            try {
                $taskProcess.Kill()
                $taskResult.kill_succeeded=$taskProcess.WaitForExit(5000)
            } catch { $taskResult.process_error=$_.Exception.Message }
        }
        $taskResult.process_exit_observed=$taskProcess.HasExited
        if ($taskResult.process_exit_observed) {
            $taskResult.native_exit_code=$taskProcess.ExitCode
            if (!$taskResult.timed_out) { $taskResult.exit_code=$taskProcess.ExitCode }
        }
        try { $taskResult.output_drained=[Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($taskStdout,$taskStderr),5000) }
        catch { $taskResult.process_error=$_.Exception.Message }
        if ($taskStdout.Status -eq [Threading.Tasks.TaskStatus]::RanToCompletion) { $taskResult.stdout=$taskStdout.Result }
        if ($taskStderr.Status -eq [Threading.Tasks.TaskStatus]::RanToCompletion) { $taskResult.stderr=$taskStderr.Result }
        if (!$taskResult.output_drained -and !$taskResult.timed_out) { $taskResult.exit_code=1 }
    } catch {
        $taskResult.launch_error=$_.Exception.Message
        if (!$taskResult.timed_out) { $taskResult.exit_code=-1 }
        if ($taskStartedProcess -and !$taskProcess.HasExited) {
            $taskResult.kill_attempted=$true
            try { $taskProcess.Kill(); $taskResult.kill_succeeded=$taskProcess.WaitForExit(5000) }
            catch { $taskResult.process_error=$_.Exception.Message }
        }
    } finally {
        $taskResult.elapsed_ms=$taskClock.ElapsedMilliseconds
        $taskClock.Stop()
        try {
            $taskWriter.WriteLine('[stdout; per-stream order preserved; cross-stream chronology is not inferred]')
            $taskWriter.Write($taskResult.stdout)
            $taskWriter.WriteLine('[stderr; driver per-capture logs retain monotonic timestamps]')
            $taskWriter.Write($taskResult.stderr)
            $taskWriter.WriteLine("[outer watchdog] timeout_ms=$TimeoutMilliseconds timed_out=$($taskResult.timed_out) kill_attempted=$($taskResult.kill_attempted) kill_succeeded=$($taskResult.kill_succeeded) exit_code=$($taskResult.exit_code)")
        } catch {
            $taskResult.process_error=$_.Exception.Message
            if (!$taskResult.timed_out) { $taskResult.exit_code=1 }
        } finally { $taskWriter.Dispose(); $taskProcess.Dispose() }
    }
    return [PSCustomObject]$taskResult
}
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
if ($OfflineValidateOnly) {
    $taskOfflineRecord=Join-Path $taskRoot 'artifacts\dla32-wch-stop-v5-tests\cancellation-r2-offline-validation.json'
    $taskSelfLog=Join-Path $taskRoot 'logs\dla32-stop-v5-cancel-r2-self-test.log'
    $taskStallLog=Join-Path $taskRoot 'logs\dla32-stop-v5-cancel-r2-watchdog-test.log'
    foreach ($taskFreshPath in @($taskOfflineRecord,$taskSelfLog,$taskStallLog)) {
        if (Test-Path -LiteralPath $taskFreshPath) { throw "Refusing to overwrite $taskFreshPath" }
    }
    $taskSelf=Invoke-TaskBoundedProcess $taskHarness @('--self-test') $taskSelfLog 20000
    $taskShell=[Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    $taskStallCode="[Console]::Out.WriteLine('watchdog stdout marker'); [Console]::Error.WriteLine('watchdog stderr marker'); Start-Sleep -Seconds 10"
    $taskStall=Invoke-TaskBoundedProcess $taskShell @('-NoProfile','-NonInteractive','-Command',$taskStallCode) $taskStallLog 1000
    $taskChecks=[ordered]@{
        selftest_native0=($taskSelf.exit_code -eq 0 -and $taskSelf.native_exit_code -eq 0)
        selftest_no_timeout=(!$taskSelf.timed_out)
        selftest_42_pass=($taskSelf.stdout -match 'PASS offline self-test: 42 checks, 0 failures')
        selftest_both_pipes_drained=$taskSelf.output_drained
        stall_timed_out=$taskStall.timed_out
        stall_outer124=($taskStall.exit_code -eq 124)
        stall_kill_acknowledged=($taskStall.kill_attempted -and $taskStall.kill_succeeded -and $taskStall.process_exit_observed)
        stall_both_pipes_drained=$taskStall.output_drained
        stall_stdout_preserved=($taskStall.stdout -match 'watchdog stdout marker')
        stall_stderr_preserved=($taskStall.stderr -match 'watchdog stderr marker')
        stall_bounded_elapsed=($taskStall.elapsed_ms -ge 1000 -and $taskStall.elapsed_ms -lt 6000)
    }
    $taskFailedChecks=@($taskChecks.GetEnumerator() | Where-Object { !$_.Value }).Count
    $taskOfflineData=[ordered]@{created_utc=[DateTimeOffset]::UtcNow.ToString('o');checks=$taskChecks.Count;failures=$taskFailedChecks;hardware_access=$false;selftest_only_harness_argument='--self-test';synthetic_child='PowerShell stdout/stderr markers then10s sleep; independent1s watchdog';production_watchdog_milliseconds=60000;wrapper_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant();original_wrapper_sha256=(Get-FileHash -LiteralPath (Join-Path $taskRoot 'tools\run_dla32_stop_v5_cancel.ps1') -Algorithm SHA256).Hash.ToLowerInvariant();harness_source_sha256=$taskExpectedSourceSha;harness_exe_sha256=$taskExpectedHarnessSha;package_manifest_sha256=$taskExpectedManifestSha;check_results=$taskChecks;selftest_result=$taskSelf;synthetic_watchdog_result=$taskStall;selftest_log_sha256=(Get-FileHash -LiteralPath $taskSelfLog -Algorithm SHA256).Hash.ToLowerInvariant();synthetic_log_sha256=(Get-FileHash -LiteralPath $taskStallLog -Algorithm SHA256).Hash.ToLowerInvariant();hardware_capture_directory_created=$false}
    $taskOfflineFile=[IO.File]::Open($taskOfflineRecord,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $taskOfflineBytes=[Text.Encoding]::UTF8.GetBytes(($taskOfflineData | ConvertTo-Json -Depth 8)); $taskOfflineFile.Write($taskOfflineBytes,0,$taskOfflineBytes.Length) } finally { $taskOfflineFile.Dispose() }
    Write-Output "Cancellation r2 offline watchdog checks=$($taskChecks.Count) failures=$taskFailedChecks; no hardware"
    if ($taskFailedChecks) { throw 'Offline watchdog checks failed; preserve logs and record' }
    return
}
$taskDrive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($taskRoot))
if ($taskDrive.AvailableFreeSpace -lt 167772160+1073741824) { throw 'Insufficient space for complete cancellation/recovery evidence' }
if (!(Test-Path -LiteralPath $taskBase)) { [IO.Directory]::CreateDirectory($taskBase) | Out-Null }
# The C harness resolves workspace/output-parent through Windows handles and
# creates only the missing final directory; no outputs are overwritten.
$taskStarted=[DateTimeOffset]::UtcNow
$taskProcessResult=Invoke-TaskBoundedProcess $taskHarness @($taskRoot,$taskOutput,$taskExpectedDllSha) $taskLog 60000
$taskCode=$taskProcessResult.exit_code
$taskLaunchError=$taskProcessResult.launch_error
$taskData=[ordered]@{policy='wch-stop-v5-sdk0';block='D-cancel-1';capture_kind='timer_cancel_then_same_open_device_finite_Buffer_Stream';output=$taskOutput;log=$taskLog;exit_code=$taskCode;launch_error=$taskLaunchError;timed_out=$taskProcessResult.timed_out;process_watchdog=$taskProcessResult;forced_termination_precludes_cleanup_claim=$taskProcessResult.timed_out;cancel_samplerate_hz=1000000;cancel_configured_sample_limit=10000000;cancel_nominal_timer_ms=100;cancel_sample_count_criterion='0 <= actual_samples < 10000000; exactly one timer stop; no LOGIC after stop';recovery_samplerate_hz=50000000;recovery_samples_per_capture=5000000;recovery_capture_count=2;threshold_volts=1.6;trigger='D0 rising';trigger_position_raw=500;physical_channel_mask='ffffffff';context='single default GLib context; same open sdi for allthree stages';dll_path=$taskDll;dll_sha256=$taskExpectedDllSha;package_manifest_sha256=$taskExpectedManifestSha;harness_sha256=$taskExpectedHarnessSha;harness_source_sha256=$taskExpectedSourceSha;harness_validation_sha256=$taskExpectedValidationSha;generator_commands_sent=$false;esp32_access=$false;started_utc=$taskStarted.ToString('o');ended_utc=[DateTimeOffset]::UtcNow.ToString('o');timer_deadline_is_not_hardware_timing_guarantee=$true;physical_integrity_verdict='offline analysis required';SDK_upload_enabled=$false;accepted_STOP_markers_required=$true;outer_exit0_required=$true}
$taskJson=$taskData | ConvertTo-Json -Depth 8
$taskFile=[IO.File]::Open($taskRecord,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $taskBytes=[Text.Encoding]::UTF8.GetBytes($taskJson); $taskFile.Write($taskBytes,0,$taskBytes.Length) } finally { $taskFile.Dispose() }
Write-Output "D-cancel-1 V5 SDK0 cancellation/recovery exit=$taskCode"
if (Test-Path -LiteralPath $taskLog) { Get-Content -LiteralPath $taskLog -Encoding UTF8 -Tail 4 }
if ($taskProcessResult.timed_out) { throw 'Independent60s watchdog timed out; exit124 recorded; do not reuse the device or start recovery after forced termination' }
if ($taskCode -ne 0) { throw 'Cancellation/recovery outer execution failed; preserve evidence and inspect before continuing' }
$taskSequence=Get-Content -LiteralPath (Join-Path $taskOutput 'sequence-result.json') -Encoding UTF8 | ConvertFrom-Json
if ($taskSequence.completed_capture_stages -ne 3 -or $taskSequence.outer_lifecycle_and_count_pass -ne $true -or
    $taskSequence.device_close_attempted -ne $true -or $taskSequence.device_close_status -ne 0 -or
    $taskSequence.sr_exit_attempted -ne $true -or $taskSequence.sr_exit_status -ne 0 -or
    $taskSequence.expected_loaded_DLL_SHA256 -ne $taskExpectedDllSha) { throw 'Cancellation/recovery sequence result failed; preserve evidence for offline analysis' }
