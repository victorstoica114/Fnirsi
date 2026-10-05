param([Parameter(Mandatory=$true)][string]$PidFile,
      [string]$Type='ComboBox', [string]$Name='', [int]$Index=0,
      [ValidateSet('Home','End','Up','Down','Enter','Escape','Tab')][string]$Key,
      [ValidateRange(1,30)][int]$Count=1)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PulseViewControlKey {
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
if ($Index -lt 0 -or $taskMatches.Count -le $Index) { throw 'Owned control missing.' }
$taskElement=$taskMatches[$Index]
if ($taskElement.Current.ProcessId -ne $taskPid) { throw 'Control identity differs.' }
if ($Type -ne 'List') { $taskElement.SetFocus() }
$taskNativePid=[uint32]0
[PulseViewControlKey]::GetWindowThreadProcessId($taskProcess.MainWindowHandle,[ref]$taskNativePid) | Out-Null
if ($taskNativePid -ne $taskPid) { throw 'Window identity differs.' }
$taskCodes=@{Home=36;End=35;Up=38;Down=40;Enter=13;Escape=27;Tab=9}
for ($taskIteration=0; $taskIteration -lt $Count; $taskIteration++) {
    if (![PulseViewControlKey]::PostMessage($taskProcess.MainWindowHandle,0x100,
        [UIntPtr][uint32]$taskCodes[$Key],[IntPtr]1)) { throw 'Keydown failed.' }
    if (![PulseViewControlKey]::PostMessage($taskProcess.MainWindowHandle,0x101,
        [UIntPtr][uint32]$taskCodes[$Key],[IntPtr]0x40000001)) { throw 'Keyup failed.' }
}
Write-Output "Posted $Count $Key keys only to owned PulseView PID $taskPid."
