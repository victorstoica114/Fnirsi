$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $projectRoot 'firmware\esp32-signal-source'
$python = 'C:\Users\User\.platformio\penv\Scripts\python.exe'
$config = 'C:\Users\User\.platformio\penv\pyvenv.cfg'

if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $config)) {
    throw 'The existing pioarduino Python environment is unavailable.'
}
$version = & $python -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
if ($LASTEXITCODE -ne 0 -or $version -ne '3.11' -or
    !(Select-String -LiteralPath $config -Pattern '^version = 3\.11\.' -Quiet)) {
    throw 'Python must match the existing Python 3.11 penv; do not run a different PlatformIO from PATH.'
}

# The ESP32 platform manages a shared penv. Use its matching interpreter and
# cached packages, without triggering dependency/core updates during this build.
$oldOffline = $env:PLATFORMIO_OFFLINE
try {
    $env:PLATFORMIO_OFFLINE = '1'
    & $python -m platformio run --project-dir $project
    $buildExit = $LASTEXITCODE
    if ($buildExit -eq 0) {
        & $python -m pip check
        $buildExit = $LASTEXITCODE
    }
} finally {
    $env:PLATFORMIO_OFFLINE = $oldOffline
}
exit $buildExit
