param([Parameter(Mandatory=$true)][string]$PidFile,
      [Parameter(Mandatory=$true)][string]$Name,
      [string]$Type='ListItem',[int]$Index=0,[switch]$RightEdge)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PulseViewControlMouse {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; }
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd,ref Point p);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd,uint msg,UIntPtr wp,IntPtr lp);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
}
'@
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'pulseview') { throw 'Owned PID is not PulseView.' }
$taskWindow=[System.Windows.Automation.AutomationElement]::FromHandle($taskProcess.MainWindowHandle)
$taskElements=$taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition)
$taskMatches=@($taskElements | Where-Object { $_.Current.Name -eq $Name -and
    $_.Current.ControlType.ProgrammaticName -eq "ControlType.$Type" -and !$_.Current.IsOffscreen })
if ($Index -lt 0 -or $taskMatches.Count -le $Index) { throw 'Owned visible element missing.' }
$taskElement=$taskMatches[$Index]
$taskBounds=$taskElement.Current.BoundingRectangle
$taskWindowBounds=$taskWindow.Current.BoundingRectangle
if ($taskElement.Current.ProcessId -ne $taskPid -or $taskBounds.Width -le 0 -or
    $taskBounds.Height -le 0 -or !$taskWindowBounds.Contains($taskBounds)) {
    throw 'Element bounds or process identity invalid.'
}
$taskNativePid=[uint32]0
[PulseViewControlMouse]::GetWindowThreadProcessId($taskProcess.MainWindowHandle,[ref]$taskNativePid) | Out-Null
if ($taskNativePid -ne $taskPid) { throw 'Window identity differs.' }
$taskPoint=New-Object PulseViewControlMouse+Point
$taskPoint.X=if ($RightEdge) { [int]($taskBounds.Right-5) } else { [int]($taskBounds.X+$taskBounds.Width/2) }
$taskPoint.Y=[int]($taskBounds.Y+$taskBounds.Height/2)
if (![PulseViewControlMouse]::ScreenToClient($taskProcess.MainWindowHandle,[ref]$taskPoint)) { throw 'Coordinate conversion failed.' }
$taskPosition=[IntPtr](($taskPoint.Y -shl 16) -bor ($taskPoint.X -band 65535))
if (![PulseViewControlMouse]::PostMessage($taskProcess.MainWindowHandle,0x201,[UIntPtr][uint32]1,$taskPosition)) { throw 'Mouse down failed.' }
if (![PulseViewControlMouse]::PostMessage($taskProcess.MainWindowHandle,0x202,[UIntPtr][uint32]0,$taskPosition)) { throw 'Mouse up failed.' }
Write-Output "Posted click only to owned PulseView PID ${taskPid}: $Name."
