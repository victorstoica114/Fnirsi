$ErrorActionPreference='Stop'
$taskProject=Split-Path $PSScriptRoot -Parent
$taskStage=Join-Path $PSScriptRoot 'esp32-build-python'
$taskSource=(Resolve-Path -LiteralPath (Join-Path $taskStage 'Lib\site-packages')).Path
$taskPenv='C:\Users\User\.platformio\penv'
$taskDestination=Join-Path $taskPenv 'Lib\site-packages'
if(-not (Test-Path -LiteralPath "$taskStage\pyvenv.cfg")) { throw 'Staging venv missing.' }
# Add/restore package files. No recursive deletion or moving; a running IDE
# process keeps its Python executable, which is deliberately left in place.
$taskRestored=0
foreach($taskFile in Get-ChildItem -LiteralPath $taskSource -Recurse -File) {
    if($taskFile.FullName -like '*\__pycache__\*') { continue }
    $taskRelative=$taskFile.FullName.Substring($taskSource.Length+1)
    $taskTarget=[IO.Path]::GetFullPath((Join-Path $taskDestination $taskRelative))
    if(-not $taskTarget.StartsWith($taskDestination+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unexpected restore destination.'
    }
    $taskParent=Split-Path $taskTarget -Parent
    if(-not (Test-Path -LiteralPath $taskParent)) {
        New-Item -ItemType Directory -Path $taskParent -Force | Out-Null
    }
    if(Test-Path -LiteralPath $taskTarget) {
        if((Get-FileHash -LiteralPath $taskTarget).Hash -eq (Get-FileHash -LiteralPath $taskFile.FullName).Hash) {
            continue
        }
    }
    Copy-Item -LiteralPath $taskFile.FullName -Destination $taskTarget -Force
    $taskRestored++
}
Copy-Item -LiteralPath "$taskStage\pyvenv.cfg" -Destination "$taskPenv\pyvenv.cfg" -Force
# Regenerate launchers for the real installation rather than copying launchers
# whose embedded interpreter points at the staging directory.
& "$taskPenv\Scripts\python.exe" -m pip install --force-reinstall --no-deps 'pioarduino==6.2.0' uv esptool==5.2.0
if($LASTEXITCODE -ne 0) { throw 'Launcher regeneration failed.' }
& "$taskPenv\Scripts\python.exe" -m pip check
if($LASTEXITCODE -ne 0) { throw 'Restored package dependency check failed.' }
& "$taskPenv\Scripts\python.exe" -m platformio --version
if($LASTEXITCODE -ne 0) { throw 'Restored core check failed.' }
Write-Output "Restored $taskRestored package files; no IDE process terminated."
