$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\cafc_daily.py"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 11:30AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries

Register-ScheduledTask `
  -TaskName "CAFC Patent Opinion Blog Daily" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Downloads PTO, DCT, and ITC Federal Circuit PDFs and rebuilds the daily patent case blog." `
  -Force

Write-Host "Registered daily CAFC Patent Opinion Blog task for 11:30 AM local time."
