$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\itc_daily.py"

# 1:45 PM with --no-trigger: scrape + sync only. The 2 PM CAFC task sends the
# single combined daily email, which by then includes this ITC content.
$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --no-trigger" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 1:45PM

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -WakeToRun `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
  -TaskName "ITC Section 337 Commission Daily" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Fetches today's ITC Section 337 commission decisions via EDIS API, AI-summarizes, syncs to Supabase (no email; the 2 PM CAFC task sends the combined digest). Wakes the machine if asleep at 1:45 PM." `
  -Force

Write-Host "Registered ITC Section 337 Commission Daily (1:45 PM, --no-trigger, wake-to-run)."
