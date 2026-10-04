param(
    [ValidateSet('Inspect','Snapshot','Click')][string]$Action='Inspect',
    [string]$Label='',
    [int]$X=0,
    [int]$Y=0,
    [string]$Output='artifacts\dla-firmware-current.png'
)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot 'artifacts\dla-firmware-app-pid.txt'))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'DLA-Logic' -or
    $taskProcess.Path -ne 'C:\Program Files\FNIRSI\DLA Logic\DLA-Logic.exe') {
    throw 'Saved PID is not the installed official FNIRSI application.'
}
$taskWindow=[System.Windows.Automation.AutomationElement]::FromHandle($taskProcess.MainWindowHandle)
$taskBounds=$taskWindow.Current.BoundingRectangle
if ($Action -eq 'Inspect') {
    $taskWindows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,$taskPid))
    foreach ($taskTop in $taskWindows) {
        Write-Output ('WINDOW: '+$taskTop.Current.Name)
        $taskTop.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {
                $taskPattern=$null
                $taskValue=$null
                if ($_.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$taskPattern)) {
                    $taskValue=$taskPattern.Current.Value
                }
                [pscustomobject]@{Name=$_.Current.Name;Type=$_.Current.ControlType.ProgrammaticName;
                    Id=$_.Current.AutomationId;Value=$taskValue;
                    X=$_.Current.BoundingRectangle.X;Y=$_.Current.BoundingRectangle.Y;
                    Width=$_.Current.BoundingRectangle.Width;Height=$_.Current.BoundingRectangle.Height;
                    Offscreen=$_.Current.IsOffscreen}
            } | ConvertTo-Json -Compress
    }
    return
}
if ($Action -eq 'Click') {
    if (!$Label) { throw 'A label observed in the current screenshot is required.' }
    if ($X -lt 0 -or $Y -lt 0 -or $X -ge $taskBounds.Width -or $Y -ge $taskBounds.Height) {
        throw 'Click must be inside the official application window.'
    }
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DlaFirmwarePointer {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
}
'@
    [DlaFirmwarePointer]::SetForegroundWindow($taskProcess.MainWindowHandle) | Out-Null
    $taskPoint=[System.Windows.Point]::new(([int]$taskBounds.X+$X),([int]$taskBounds.Y+$Y))
    $taskElement=[System.Windows.Automation.AutomationElement]::FromPoint($taskPoint)
    if ($taskElement.Current.ProcessId -ne $taskPid) { throw 'Target point belongs to another application.' }
    [DlaFirmwarePointer]::SetCursorPos([int]$taskPoint.X,[int]$taskPoint.Y) | Out-Null
    [DlaFirmwarePointer]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
    [DlaFirmwarePointer]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
    Write-Output ('Clicked observed control: '+$Label+'; window point '+$X+','+$Y)
    return
}
$taskBitmap=New-Object System.Drawing.Bitmap([int]$taskBounds.Width,[int]$taskBounds.Height)
$taskGraphics=[System.Drawing.Graphics]::FromImage($taskBitmap)
try {
    $taskGraphics.CopyFromScreen([int]$taskBounds.X,[int]$taskBounds.Y,0,0,$taskBitmap.Size)
    $taskBitmap.Save((Join-Path $taskRoot $Output),[System.Drawing.Imaging.ImageFormat]::Png)
} finally { $taskGraphics.Dispose(); $taskBitmap.Dispose() }
Write-Output $Output
