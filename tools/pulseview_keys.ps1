param([string]$Keys='', [string]$Screenshot='', [string]$PidFile='artifacts\pulseview-pid.txt')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PulseViewNativeUi {
 public delegate bool EnumProc(IntPtr hwnd,IntPtr data);
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd,out Rect rect);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern IntPtr GetLastActivePopup(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback,IntPtr data);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
}
'@
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'pulseview' -or !$taskProcess.MainWindowHandle) { throw 'PulseView window missing.' }
if ($Keys) {
    $taskTarget=[PulseViewNativeUi]::GetLastActivePopup($taskProcess.MainWindowHandle)
    if (![PulseViewNativeUi]::IsWindowVisible($taskTarget)) {
        $script:taskVisiblePopup=[IntPtr]::Zero
        $taskCallback=[PulseViewNativeUi+EnumProc]{ param($taskHwnd,$taskData)
            $taskWindowPid=[uint32]0
            [PulseViewNativeUi]::GetWindowThreadProcessId($taskHwnd,[ref]$taskWindowPid) | Out-Null
            if ($taskWindowPid -eq $taskPid -and $taskHwnd -ne $taskProcess.MainWindowHandle -and
                [PulseViewNativeUi]::IsWindowVisible($taskHwnd)) { $script:taskVisiblePopup=$taskHwnd }
            return $true
        }
        [PulseViewNativeUi]::EnumWindows($taskCallback,[IntPtr]::Zero) | Out-Null
        if ($script:taskVisiblePopup -ne [IntPtr]::Zero) { $taskTarget=$script:taskVisiblePopup }
        else { $taskTarget=$taskProcess.MainWindowHandle }
    }
    [PulseViewNativeUi]::SetForegroundWindow($taskTarget) | Out-Null
    $taskForegroundPid=[uint32]0
    [PulseViewNativeUi]::GetWindowThreadProcessId([PulseViewNativeUi]::GetForegroundWindow(),[ref]$taskForegroundPid) | Out-Null
    if ($taskForegroundPid -ne $taskPid) { throw 'PulseView is not foreground; no keys sent.' }
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
}
if ($Screenshot) {
    $taskRect=New-Object PulseViewNativeUi+Rect
    if (![PulseViewNativeUi]::GetWindowRect($taskProcess.MainWindowHandle,[ref]$taskRect)) { throw 'Window bounds unavailable.' }
    $taskBitmap=New-Object System.Drawing.Bitmap(($taskRect.Right-$taskRect.Left),($taskRect.Bottom-$taskRect.Top))
    $taskGraphics=[System.Drawing.Graphics]::FromImage($taskBitmap)
    try {
        $taskGraphics.CopyFromScreen($taskRect.Left,$taskRect.Top,0,0,$taskBitmap.Size)
        $taskBitmap.Save((Join-Path $taskRoot $Screenshot),[System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $taskGraphics.Dispose(); $taskBitmap.Dispose() }
    Write-Output $Screenshot
}
