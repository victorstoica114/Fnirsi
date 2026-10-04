<# Hardware-free: parse V5 SDK0 runner and evaluate only its pure package predicate. #>
$ErrorActionPreference='Stop'
$v5Root=Split-Path -Parent $PSScriptRoot
$v5Runner=Join-Path $PSScriptRoot 'run_dla32_wch_stop_v5_sdk0.ps1'
$v5Tokens=$null; $v5Errors=$null
$v5Ast=[Management.Automation.Language.Parser]::ParseFile($v5Runner,[ref]$v5Tokens,[ref]$v5Errors)
if ($v5Errors.Count -ne 0) { throw "Runner parser error: $v5Errors" }
$v5Text=Get-Content -LiteralPath $v5Runner -Raw
$v5Match=[regex]::Match($v5Text,"if \((\`$taskManifest.variant[\s\S]+?)\) \{ throw 'Package provenance or tested V5 SDK0 configuration differs' \}")
if (!$v5Match.Success) { throw 'Cannot locate isolated predicate' }
$v5Predicate=$v5Match.Groups[1].Value
$v5ManifestText=Get-Content -LiteralPath (Join-Path $v5Root 'artifacts\dla32-wch-stop-v5-sdk0\package-manifest.json') -Raw
$taskSha=(Get-FileHash -LiteralPath (Join-Path $v5Root 'artifacts\dla32-wch-stop-v5-sdk0\libsigrok-4.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
$v5Cases=@(
    @{label='valid frozen V5 SDK0 package accepted';edit={};reject=$false},
    @{label='wrong SDK1 policy rejected';edit={$taskManifest.sdk_policy=1};reject=$true},
    @{label='failed lifecycle fixture rejected';edit={$taskManifest.unit_failures=1};reject=$true},
    @{label='missing new failure coverage rejected';edit={$taskManifest.unit_checks=444};reject=$true},
    @{label='untested explicit0 rejected';edit={$taskManifest.sdk0_explicit_macro_unit_checks=444};reject=$true},
    @{label='failed timeout-helper fixture rejected';edit={$taskManifest.timeout_helper_failures=1};reject=$true},
    @{label='native build failure rejected';edit={$taskManifest.native_bash_exit_code=1};reject=$true},
    @{label='two-empty prearm policy rejected';edit={$taskManifest.prearm_empty_reads=2};reject=$true},
    @{label='added upload exports rejected';edit={$taskManifest.sdk_upload_exports_required=$true};reject=$true},
    @{label='fictitious SDK-disable STOP marker rejected';edit={$taskManifest.sdk_disable_precedes_hardware_stop=$true};reject=$true},
    @{label='missing pendingSTOP ownership rejected';edit={$taskManifest.sdk0_pending_stop_retention=$false};reject=$true},
    @{label='missing config/PWM guard rejected';edit={$taskManifest.wch_configuration_pwm_blocked_while_transport_ownership_pending=$false};reject=$true},
    @{label='changed API rejected';edit={$taskManifest.api_sha256='modified'};reject=$true},
    @{label='changed transport header rejected';edit={$taskManifest.transport_header_sha256='modified'};reject=$true},
    @{label='short write deadline rejected';edit={$taskManifest.command_write_timeout_ms=20};reject=$true},
    @{label='SDK1 old log policy altered rejected';edit={$taskManifest.sdk1_success_markers_and_order_preserved=$false};reject=$true},
    @{label='changed libusb behavior rejected';edit={$taskManifest.libusb_stop_cancel_behavior_unchanged=$false};reject=$true},
    @{label='wrong runtime DLL hash rejected';edit={$taskManifest.files.'libsigrok-4.dll'.sha256='modified'};reject=$true}
)
foreach ($v5Case in $v5Cases) {
    $taskManifest=$v5ManifestText | ConvertFrom-Json
    . $v5Case.edit
    $v5Rejected=[bool](Invoke-Expression $v5Predicate)
    if ($v5Rejected -ne $v5Case.reject) { throw "FAIL: $($v5Case.label)" }
    Write-Output "PASS: $($v5Case.label)"
}
Write-Output "V5 runner guard audit: $($v5Cases.Count) checks, 0 failures. AST parsed; no runner/hardware execution."
