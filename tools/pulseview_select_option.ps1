param([Parameter(Mandatory=$true)][string]$PidFile,
      [Parameter(Mandatory=$true)][string]$Option,[int]$Index=0)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PulseViewOptionNative {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; }
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd,ref Point p);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd,uint msg,UIntPtr wp,IntPtr lp);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
}
'@
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
function Get-TaskWindow {
    $taskProcess=Get-Process -Id $taskPid
    if ($taskProcess.ProcessName -ne 'pulseview') { throw 'Owned process differs.' }
    $script:taskHandle=$taskProcess.MainWindowHandle
    $taskNativePid=[uint32]0
    [PulseViewOptionNative]::GetWindowThreadProcessId($script:taskHandle,[ref]$taskNativePid) | Out-Null
    if ($taskNativePid -ne $taskPid) { throw 'Window identity differs.' }
    return [System.Windows.Automation.AutomationElement]::FromHandle($script:taskHandle)
}
function Get-TaskElements($taskWindow) {
    return $taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
}
$taskWindow=Get-TaskWindow
$taskCombos=@((Get-TaskElements $taskWindow) | Where-Object { $_.Current.ControlType.ProgrammaticName -eq 'ControlType.ComboBox' -and !$_.Current.IsOffscreen })
if ($Index -lt 0 -or $Index -ge $taskCombos.Count) { throw 'Combo missing.' }
$taskCombos[$Index].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Milliseconds 100
$taskItem=$null
foreach ($taskKey in @(0,35,36)) {
    $taskWindow=Get-TaskWindow
    if ($taskKey) {
        if (![PulseViewOptionNative]::PostMessage($script:taskHandle,0x100,[UIntPtr][uint32]$taskKey,[IntPtr]1)) { throw 'List key failed.' }
        [PulseViewOptionNative]::PostMessage($script:taskHandle,0x101,[UIntPtr][uint32]$taskKey,[IntPtr]0x40000001) | Out-Null
        Start-Sleep -Milliseconds 100
    }
    $taskItems=@((Get-TaskElements $taskWindow) | Where-Object { $_.Current.Name -eq $Option -and $_.Current.ControlType.ProgrammaticName -eq 'ControlType.ListItem' -and !$_.Current.IsOffscreen })
    if ($taskItems.Count -eq 1 -and $taskWindow.Current.BoundingRectangle.Contains($taskItems[0].Current.BoundingRectangle)) { $taskItem=$taskItems[0]; break }
}
if (!$taskItem) { throw 'Visible option unavailable.' }
$taskBounds=$taskItem.Current.BoundingRectangle
$taskPoint=New-Object PulseViewOptionNative+Point
$taskPoint.X=[int]($taskBounds.X+$taskBounds.Width/2)
$taskPoint.Y=[int]($taskBounds.Y+$taskBounds.Height/2)
if (![PulseViewOptionNative]::ScreenToClient($script:taskHandle,[ref]$taskPoint)) { throw 'Coordinate conversion failed.' }
$taskPosition=[IntPtr](($taskPoint.Y -shl 16) -bor ($taskPoint.X -band 65535))
[PulseViewOptionNative]::PostMessage($script:taskHandle,0x201,[UIntPtr][uint32]1,$taskPosition) | Out-Null
[PulseViewOptionNative]::PostMessage($script:taskHandle,0x202,[UIntPtr][uint32]0,$taskPosition) | Out-Null
Start-Sleep -Milliseconds 100
$taskWindow=Get-TaskWindow
$taskCombos=@((Get-TaskElements $taskWindow) | Where-Object { $_.Current.ControlType.ProgrammaticName -eq 'ControlType.ComboBox' -and !$_.Current.IsOffscreen })
if ($Index -ge $taskCombos.Count) { throw 'Selector missing after click.' }
$taskActual=$taskCombos[$Index].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
if ($taskActual -ne $Option) { throw "Selector differs: $taskActual" }
[pscustomobject]@{PID=$taskPid;Index=$Index;Requested=$Option;Actual=$taskActual;Verified=$true} | ConvertTo-Json -Compress
