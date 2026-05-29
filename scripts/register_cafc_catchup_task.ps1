$ErrorActionPreference = "Stop"

# Afternoon catch-up for the CAFC blog. CAFC posts opinions/orders throughout
# the day; the 2:00 PM run can miss anything posted later (this is exactly how
# the Future Link v. Realtek order was missed by the old 11:30 AM run). This
# 6:00 PM run re-scrapes the live page and upserts any opinions posted after
# 2 PM onto the blog. It also triggers the digest endpoint, but that is a
# no-op when the 2 PM email already went out (daily_digest_log status='sent'
# hard-skips) — so it never sends a second email; it only acts as a backstop
# if the 2 PM run was missed entirely.

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = (Get-Command python).Source
$script = Join-Path $projectRoot "scripts\cafc_daily.py"

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -WakeToRun `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
  -TaskName "CAFC Patent Opinion Blog Catch-up" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "6:00 PM catch-up scrape: captures CAFC opinions/orders posted after the 2:00 PM run onto the blog. Digest trigger is a no-op if the 2 PM email already sent (idempotency), so no duplicate email is sent." `
  -Force

Write-Host "Registered CAFC Patent Opinion Blog Catch-up (6:00 PM, wake-to-run, run-if-missed)."
