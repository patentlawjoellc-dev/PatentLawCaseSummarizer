$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\ptab_precedential_daily.py"

# 1:50 PM: posts a Beehiiv breaking-news post for newly designated precedential
# decisions (independent of the 2 PM email digest). Kept in the afternoon batch.
$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 1:50PM

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -WakeToRun `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
  -TaskName "PTAB Precedential Decisions Daily" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Checks for newly designated PTAB precedential/informative decisions, AI-summarizes, and syncs to Supabase. Triggers breaking-news Beehiiv post when new precedential decisions are found." `
  -Force

Write-Host "Registered PTAB Precedential Decisions Daily (1:50 PM, wake-to-run, run-if-missed)."
