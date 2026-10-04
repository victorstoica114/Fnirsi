<# Hardware-free: evaluate only the new runner's pure configuration predicate.
   Never run the runner or its process/device/capture operations. #>
$ErrorActionPreference='Stop'
$v4Root=Split-Path -Parent $PSScriptRoot
$v4Runner=Join-Path $PSScriptRoot 'run_dla32_wch_upload_v4.ps1'
$v4Tokens=$null; $v4Errors=$null
$v4Ast=[Management.Automation.Language.Parser]::ParseFile($v4Runner,[ref]$v4Tokens,[ref]$v4Errors)
if ($v4Errors.Count -ne 0) { throw "Runner parser error: $v4Errors" }
$v4Text=Get-Content -LiteralPath $v4Runner -Raw
$v4Match=[regex]::Match($v4Text,"if \((\`$taskManifest.variant[\s\S]+?)\) \{ throw 'Package provenance or tested V4 configuration differs' \}")
if (!$v4Match.Success) { throw 'Cannot find isolated configuration predicate' }
$v4Predicate=$v4Match.Groups[1].Value
$v4ManifestText=Get-Content -LiteralPath (Join-Path $v4Root 'artifacts\dla32-wch-upload-v4\package-manifest.json') -Raw
$taskSha=(Get-FileHash -LiteralPath (Join-Path $v4Root 'artifacts\dla32-wch-upload-v4\libsigrok-4.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
$v4Cases=@(
    @{label='valid frozen V4 configuration accepted';edit={};reject=$false},
    @{label='failed lifecycle fixture rejected';edit={$taskManifest.unit_failures=1};reject=$true},
    @{label='missing interaction coverage rejected';edit={$taskManifest.unit_checks=534};reject=$true},
    @{label='failed timeout helper fixture rejected';edit={$taskManifest.timeout_helper_failures=1};reject=$true},
    @{label='native build failure rejected';edit={$taskManifest.native_bash_exit_code=1};reject=$true},
    @{label='two-empty diagnostic rejected';edit={$taskManifest.prearm_empty_reads=2};reject=$true},
    @{label='changed API rejected';edit={$taskManifest.api_sha256='modified'};reject=$true},
    @{label='old timeout policy rejected';edit={$taskManifest.timeout_policy_unchanged=$true};reject=$true},
    @{label='short write deadline rejected';edit={$taskManifest.command_write_timeout_ms=20};reject=$true},
    @{label='changed sample read size rejected';edit={$taskManifest.read_size_bytes=524288};reject=$true},
    @{label='additional SDK0 STOP correction rejected';edit={$taskManifest.sdk0_pending_stop_generalization_included=$true};reject=$true},
    @{label='wrong runtime DLL hash rejected';edit={$taskManifest.files.'libsigrok-4.dll'.sha256='modified'};reject=$true}
)
foreach ($v4Case in $v4Cases) {
    $taskManifest=$v4ManifestText | ConvertFrom-Json
    . $v4Case.edit
    $v4Rejected=[bool](Invoke-Expression $v4Predicate)
    if ($v4Rejected -ne $v4Case.reject) { throw "FAIL: $($v4Case.label)" }
    Write-Output "PASS: $($v4Case.label)"
}
Write-Output "Runner guard audit: $($v4Cases.Count) checks, 0 failures. AST parsed; no runner/hardware execution."
