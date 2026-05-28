$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\cafc_daily.py"

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 11:30AM

# Critical settings:
#   -StartWhenAvailable    : if the trigger time was missed (laptop closed), run as soon as the machine wakes
#   -WakeToRun             : wake the machine from sleep to run on time
#   -AllowStartIfOnBatteries / -DontStopIfGoingOnBatteries : don't skip on battery power
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -WakeToRun `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
  -TaskName "CAFC Patent Opinion Blog Daily" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Downloads PTO, DCT, and ITC Federal Circuit PDFs and rebuilds the daily patent case blog. Wakes the machine if asleep at 11:30 AM and runs missed triggers when the machine starts." `
  -Force

Write-Host "Registered CAFC Patent Opinion Blog Daily (11:30 AM, wake-to-run, run-if-missed)."
