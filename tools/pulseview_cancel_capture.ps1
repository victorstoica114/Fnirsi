param([Parameter(Mandatory=$true)][string]$PidFile,
      [Parameter(Mandatory=$true)][string]$Record,
      [ValidateRange(100,1000)][int]$DelayMs=300)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PulseViewCancelNative {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; }
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd,ref Point p);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd,uint msg,UIntPtr wp,IntPtr lp);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
}
'@
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskRecord=Join-Path $taskRoot $Record
if (Test-Path -LiteralPath $taskRecord) { throw 'Fresh result required.' }
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'pulseview') { throw 'Owned process differs.' }
$taskHandle=$taskProcess.MainWindowHandle
$taskNativePid=[uint32]0
[PulseViewCancelNative]::GetWindowThreadProcessId($taskHandle,[ref]$taskNativePid) | Out-Null
if ($taskNativePid -ne $taskPid) { throw 'Window identity differs.' }
$taskWindow=[System.Windows.Automation.AutomationElement]::FromHandle($taskHandle)
function Get-TaskButton([string]$taskName) {
    $taskAll=$taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
    return @($taskAll | Where-Object { $_.Current.Name -eq $taskName -and $_.Current.ControlType.ProgrammaticName -eq 'ControlType.Button' -and !$_.Current.IsOffscreen })
}
function Send-TaskClick($taskElement) {
    $taskBounds=$taskElement.Current.BoundingRectangle
    if ($taskElement.Current.ProcessId -ne $taskPid -or !$taskWindow.Current.BoundingRectangle.Contains($taskBounds)) { throw 'Button identity/bounds invalid.' }
    $taskPoint=New-Object PulseViewCancelNative+Point
    $taskPoint.X=[int]($taskBounds.X+$taskBounds.Width/2)
    $taskPoint.Y=[int]($taskBounds.Y+$taskBounds.Height/2)
    if (![PulseViewCancelNative]::ScreenToClient($taskHandle,[ref]$taskPoint)) { throw 'Coordinate conversion failed.' }
    $taskPosition=[IntPtr](($taskPoint.Y -shl 16) -bor ($taskPoint.X -band 65535))
    if (![PulseViewCancelNative]::PostMessage($taskHandle,0x201,[UIntPtr][uint32]1,$taskPosition)) { throw 'Mouse down failed.' }
    if (![PulseViewCancelNative]::PostMessage($taskHandle,0x202,[UIntPtr][uint32]0,$taskPosition)) { throw 'Mouse up failed.' }
}
$taskRun=Get-TaskButton 'Run'
if ($taskRun.Count -ne 1) { throw 'Run unavailable.' }
$taskTimer=[System.Diagnostics.Stopwatch]::StartNew()
Send-TaskClick $taskRun[0]
Start-Sleep -Milliseconds $DelayMs
$taskStop=Get-TaskButton 'Stop'
if ($taskStop.Count -ne 1) { throw 'Stop unavailable; refusing to click Run again.' }
$taskStopPosted=$taskTimer.Elapsed.TotalMilliseconds
Send-TaskClick $taskStop[0]
$taskIdle=$false
while ($taskTimer.Elapsed.TotalSeconds -lt 8) {
    if ((Get-TaskButton 'Run').Count -eq 1) { $taskIdle=$true; break }
    Start-Sleep -Milliseconds 50
}
$taskResult=[pscustomobject]@{PID=$taskPid;RequestedDelayMs=$DelayMs;StopPostedAfterMs=$taskStopPosted;
    IdleAfterMs=$taskTimer.Elapsed.TotalMilliseconds;ReturnedToRun=$taskIdle;
    PreciseHardwareTriggerOrStopTimingValidated=$false;SourceOutputsStopped=$false}
$taskResult | ConvertTo-Json | Set-Content -LiteralPath $taskRecord -Encoding UTF8
$taskResult | ConvertTo-Json -Compress
if (!$taskIdle) { throw 'GUI did not return to Run within bound.' }
