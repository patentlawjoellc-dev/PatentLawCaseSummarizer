$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\ptab_daily.py"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 12:30PM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries

Register-ScheduledTask `
  -TaskName "PTAB Director Decisions Daily" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Fetches today's PTAB director institution decisions, AI-summarizes substantive decisions, and syncs to Supabase." `
  -Force

Write-Host "Registered daily PTAB Director Decisions task for 12:30 PM local time."
