param(
    [ValidateSet('Inspect','Click','Toggle','Select','SetValue','GetValue','Snapshot','Close')]
    [string]$Action='Inspect',
    [string]$Name='',
    [string]$Value='',
    [int]$Index=0,
    [string]$Type='',
    [string]$Output='artifacts\pulseview-current.png',
    [string]$PidFile='artifacts\pulseview-pid.txt'
)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPid=[int](Get-Content -LiteralPath (Join-Path $taskRoot $PidFile))
$taskProcess=Get-Process -Id $taskPid
if ($taskProcess.ProcessName -ne 'pulseview') { throw 'Saved PID is not PulseView.' }
$taskWindow=[System.Windows.Automation.AutomationElement]::FromHandle($taskProcess.MainWindowHandle)
if ($Action -eq 'Close') {
    $taskWindow.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
    return
}
if ($Action -eq 'Snapshot') {
    Add-Type -AssemblyName System.Drawing
    $taskBounds=$taskWindow.Current.BoundingRectangle
    $taskBitmap=New-Object System.Drawing.Bitmap([int]$taskBounds.Width,[int]$taskBounds.Height)
    $taskGraphics=[System.Drawing.Graphics]::FromImage($taskBitmap)
    try {
        $taskGraphics.CopyFromScreen([int]$taskBounds.X,[int]$taskBounds.Y,0,0,$taskBitmap.Size)
        $taskBitmap.Save((Join-Path $taskRoot $Output),[System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $taskGraphics.Dispose(); $taskBitmap.Dispose() }
    Write-Output $Output
    return
}
$taskElements=$taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition)
if ($Action -eq 'Inspect') {
    $taskElements | ForEach-Object {
        $taskRect=$_.Current.BoundingRectangle
        [PSCustomObject]@{Name=$_.Current.Name;Type=$_.Current.ControlType.ProgrammaticName;
            Help=$_.Current.HelpText;X=$taskRect.X;Y=$taskRect.Y;
            Width=$taskRect.Width;Height=$taskRect.Height;Offscreen=$_.Current.IsOffscreen}
    } | Format-Table -AutoSize
    return
}
$taskMatches=@($taskElements | Where-Object { $_.Current.Name -eq $Name -and
    (!$Type -or $_.Current.ControlType.ProgrammaticName -eq "ControlType.$Type") })
if ($taskMatches.Count -le $Index) { throw "UI element missing: $Name [$Index]" }
$taskElement=$taskMatches[$Index]
switch ($Action) {
    'GetValue' {
        $taskPattern=$null
        if ($taskElement.TryGetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern,[ref]$taskPattern)) {
            $taskPattern.Current | Select-Object Value,Minimum,Maximum
        } elseif ($taskElement.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$taskPattern)) {
            $taskPattern.Current | Select-Object Value,IsReadOnly
        } else { throw 'Value pattern unavailable.' }
    }
    'Click' { $taskElement.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
    'Toggle' { $taskElement.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() }
    'Select' { $taskElement.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select() }
    'SetValue' {
        $taskPattern=$null
        if ($taskElement.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$taskPattern)) {
            $taskPattern.SetValue($Value)
        } else {
            $taskElement.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern).SetValue([double]$Value)
        }
    }
}
