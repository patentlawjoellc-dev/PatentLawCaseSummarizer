$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\itc_daily.py"

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 1:00PM

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
  -Description "Fetches today's ITC Section 337 commission decisions via EDIS API, AI-summarizes, syncs to Supabase." `
  -Force

Write-Host "Registered ITC Section 337 Commission Daily (1:00 PM, wake-to-run, run-if-missed)."
