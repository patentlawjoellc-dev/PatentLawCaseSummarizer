$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\ptab_daily.py"

# 1:40 PM with --no-trigger: scrape + sync only. The daily email is sent once,
# by the CAFC task at 2:00 PM, which by then sees this PTAB content too.
$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --no-trigger" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 1:40PM

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -WakeToRun `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
  -TaskName "PTAB Director Decisions Daily" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Fetches today's PTAB director institution decisions, AI-summarizes substantive decisions, and syncs to Supabase (no email; the 2 PM CAFC task sends the combined digest). Wakes the machine if asleep at 1:40 PM." `
  -Force

Write-Host "Registered PTAB Director Decisions Daily (1:40 PM, --no-trigger, wake-to-run)."
