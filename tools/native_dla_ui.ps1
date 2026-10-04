param(
    [ValidateSet('Inspect','Snapshot','Click','Keys','SetValue')][string]$Action='Inspect',
    [string]$Label='', [string]$Keys='', [string]$Name='', [string]$Value='',
    [string]$ExpectedDialog='',
    [int]$Index=0, [int]$X=0, [int]$Y=0,
    [string]$Output='artifacts\native-dla-current.png',
    [string]$PidFile='artifacts\native-dla-app-pid.txt'
)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NativeDlaUi {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern IntPtr GetLastActivePopup(IntPtr handle);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle,out uint pid);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
}
'@
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'DLA-Logic' -or $taskProcess.Path -ne 'C:\Program Files\FNIRSI\DLA Logic\DLA-Logic.exe' -or !$taskProcess.MainWindowHandle) {
    throw 'Saved PID is not the installed official FNIRSI application window.'
}
$taskTarget=[NativeDlaUi]::GetLastActivePopup($taskProcess.MainWindowHandle)
if (![NativeDlaUi]::IsWindowVisible($taskTarget)) { $taskTarget=$taskProcess.MainWindowHandle }
$taskTargetPid=[uint32]0
[NativeDlaUi]::GetWindowThreadProcessId($taskTarget,[ref]$taskTargetPid) | Out-Null
if ($taskTargetPid -ne $taskPid) { throw 'Active popup belongs to another process.' }
$taskWindow=[System.Windows.Automation.AutomationElement]::FromHandle($taskTarget)
$taskBounds=$taskWindow.Current.BoundingRectangle
if ($Action -eq 'Inspect') {
    $taskWindows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$taskPid))
    foreach ($taskTop in $taskWindows) {
        Write-Output ('WINDOW: '+$taskTop.Current.Name)
        $taskTop.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {
            $taskPattern=$null; $taskValue=$null
            if ($_.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$taskPattern)) { $taskValue=$taskPattern.Current.Value }
            [pscustomobject]@{Name=$_.Current.Name;Type=$_.Current.ControlType.ProgrammaticName;Value=$taskValue;Id=$_.Current.AutomationId;X=$_.Current.BoundingRectangle.X;Y=$_.Current.BoundingRectangle.Y;Width=$_.Current.BoundingRectangle.Width;Height=$_.Current.BoundingRectangle.Height;Offscreen=$_.Current.IsOffscreen}
        } | ConvertTo-Json -Compress
    }
    return
}
$taskForegroundPid=[uint32]0
[NativeDlaUi]::GetWindowThreadProcessId([NativeDlaUi]::GetForegroundWindow(),[ref]$taskForegroundPid) | Out-Null
if ($taskForegroundPid -ne $taskPid) {
    [NativeDlaUi]::SetForegroundWindow($taskTarget) | Out-Null
    Start-Sleep -Milliseconds 150
    [NativeDlaUi]::GetWindowThreadProcessId([NativeDlaUi]::GetForegroundWindow(),[ref]$taskForegroundPid) | Out-Null
}
if ($taskForegroundPid -ne $taskPid) { throw 'FNIRSI app is not foreground; action refused.' }
if ($Action -eq 'Click') {
    if (!$Label -or $X -lt 0 -or $Y -lt 0 -or $X -ge $taskBounds.Width -or $Y -ge $taskBounds.Height) { throw 'Observed label and in-window point required.' }
    $taskPoint=[System.Windows.Point]::new(([int]$taskBounds.X+$X),([int]$taskBounds.Y+$Y))
    $taskElement=[System.Windows.Automation.AutomationElement]::FromPoint($taskPoint)
    if ($taskElement.Current.ProcessId -ne $taskPid) { throw 'Observed point belongs to another process.' }
    [NativeDlaUi]::SetCursorPos([int]$taskPoint.X,[int]$taskPoint.Y) | Out-Null
    [NativeDlaUi]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
    [NativeDlaUi]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
    Write-Output "Clicked $Label at $X,$Y"
    return
}
if ($Action -eq 'Keys') {
    if (!$Keys -or !$Label) { throw 'Keys and observed target/shortcut label required.' }
    if ($ExpectedDialog) {
        $taskDialogCondition=[System.Windows.Automation.AndCondition]::new(
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$taskPid),
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,$ExpectedDialog))
        $taskDialog=$null
        for ($taskDialogAttempt=0;$taskDialogAttempt -lt 20;$taskDialogAttempt++) {
            $taskDialog=[System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$taskDialogCondition)
            if ($taskDialog -and !$taskDialog.Current.IsOffscreen) { break }
            Start-Sleep -Milliseconds 250
        }
        if (!$taskDialog -or $taskDialog.Current.IsOffscreen) { throw "Expected visible file dialog missing: $ExpectedDialog" }
    }
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
    Write-Output "Sent keys to observed target: $Label"
    return
}
if ($Action -eq 'SetValue') {
    if (!$Name) { throw 'Observed UIA element name required.' }
    $taskMatches=@($taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { $_.Current.Name -eq $Name })
    if ($taskMatches.Count -le $Index) { throw "UI element missing: $Name" }
    $taskMatches[$Index].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($Value)
    return
}
$taskOutputPath=[IO.Path]::GetFullPath((Join-Path $taskRoot $Output))
if (!$taskOutputPath.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Screenshot must remain in workspace.' }
$taskBitmap=New-Object System.Drawing.Bitmap([int]$taskBounds.Width,[int]$taskBounds.Height)
$taskGraphics=[System.Drawing.Graphics]::FromImage($taskBitmap)
try {
    $taskGraphics.CopyFromScreen([int]$taskBounds.X,[int]$taskBounds.Y,0,0,$taskBitmap.Size)
    $taskBitmap.Save($taskOutputPath,[System.Drawing.Imaging.ImageFormat]::Png)
} finally { $taskGraphics.Dispose(); $taskBitmap.Dispose() }
Write-Output $Output
