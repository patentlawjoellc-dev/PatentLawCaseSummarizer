$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\cafc_daily.py"

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
# 2:00 PM: CAFC runs LAST in the afternoon batch and is the single trigger for
# the daily email digest — by now PTAB/ITC have already synced (1:40/1:45,
# --no-trigger), so the 2 PM email contains all of that day's content. Moving
# from 11:30 AM also catches opinions/orders posted later in the morning
# (e.g. the Future Link v. Realtek order that was posted after the old scrape).
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00PM

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
  -Description "Downloads PTO, DCT, and ITC Federal Circuit PDFs, rebuilds the daily patent case blog, and sends the 2 PM daily email digest (includes PTAB/ITC content synced earlier in the afternoon). Wakes the machine if asleep at 2:00 PM and runs missed triggers when the machine starts." `
  -Force

Write-Host "Registered CAFC Patent Opinion Blog Daily (2:00 PM, wake-to-run, run-if-missed)."
