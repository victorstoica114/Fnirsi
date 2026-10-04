param([string]$Screenshot='', [string]$PidFile='artifacts\pulseview-pid.txt')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'pulseview') { throw 'PID is not PulseView.' }
$taskWindow=[System.Windows.Automation.AutomationElement]::FromHandle($taskProcess.MainWindowHandle)
$taskElements=$taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition)
if ($Screenshot) {
    Add-Type -AssemblyName System.Drawing
    $taskRect=($taskElements | Where-Object { !$_.Current.IsOffscreen -and
            $_.Current.BoundingRectangle.Width -gt 0 -and $_.Current.BoundingRectangle.Width -lt 16384 -and
            $_.Current.BoundingRectangle.Height -gt 0 -and $_.Current.BoundingRectangle.Height -lt 16384 } |
        Sort-Object { $_.Current.BoundingRectangle.Width * $_.Current.BoundingRectangle.Height } -Descending |
        Select-Object -First 1).Current.BoundingRectangle
    $taskBitmap=New-Object System.Drawing.Bitmap([int]$taskRect.Width,[int]$taskRect.Height)
    $taskGraphics=[System.Drawing.Graphics]::FromImage($taskBitmap)
    try {
        $taskGraphics.CopyFromScreen([int]$taskRect.X,[int]$taskRect.Y,0,0,$taskBitmap.Size)
        $taskBitmap.Save((Join-Path $taskRoot $Screenshot),[System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $taskGraphics.Dispose(); $taskBitmap.Dispose() }
}
$taskElements | Where-Object { $_.Current.ControlType.ProgrammaticName -match 'Button|ComboBox|Edit|TabItem' } |
    ForEach-Object {
        $taskRect=$_.Current.BoundingRectangle
        $taskValue=$null
        $taskPattern=$null
        if ($_.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$taskPattern)) {
            $taskValue=$taskPattern.Current.Value
        }
        [PSCustomObject]@{Name=$_.Current.Name;Type=$_.Current.ControlType.ProgrammaticName;
            Help=$_.Current.HelpText;Value=$taskValue;X=$taskRect.X;Y=$taskRect.Y;
            Width=$taskRect.Width;Height=$taskRect.Height;Offscreen=$_.Current.IsOffscreen}
    } | ConvertTo-Json -Compress
