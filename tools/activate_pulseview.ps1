param([string]$PidFile='artifacts\pulseview-pid.txt')
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class Dla32ActivateWindow {
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int command);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
}
'@
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'pulseview' -or $taskProcess.MainWindowTitle -notlike '*PulseView') {
    throw 'Cannot identify the main PulseView window.'
}
[Dla32ActivateWindow]::ShowWindow($taskProcess.MainWindowHandle,9) | Out-Null
[Dla32ActivateWindow]::SetForegroundWindow($taskProcess.MainWindowHandle) | Out-Null
$taskForegroundPid=[uint32]0
[Dla32ActivateWindow]::GetWindowThreadProcessId([Dla32ActivateWindow]::GetForegroundWindow(),[ref]$taskForegroundPid) | Out-Null
if ($taskForegroundPid -ne $taskPid) { throw 'PulseView activation was unsuccessful.' }
Write-Output "Activated PulseView PID $taskPid."
