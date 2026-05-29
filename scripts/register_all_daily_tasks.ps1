$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Registering all four daily Patent Law Professor tasks..." -ForegroundColor Cyan
Write-Host ""

& (Join-Path $here "register_ptab_daily_task.ps1")        # 1:40 PM  (sync only, --no-trigger)
& (Join-Path $here "register_itc_daily_task.ps1")         # 1:45 PM  (sync only, --no-trigger)
& (Join-Path $here "register_ptab_precedential_task.ps1") # 1:50 PM  (Beehiiv breaking-news)
& (Join-Path $here "register_daily_task.ps1")             # 2:00 PM  (scrape + sends the combined daily email)
& (Join-Path $here "register_cafc_catchup_task.ps1")      # 6:00 PM  (catch late opinions onto the blog)

Write-Host ""
Write-Host "All four tasks registered. Current state:" -ForegroundColor Green
Get-ScheduledTask | Where-Object {
  $_.TaskName -in @(
    "CAFC Patent Opinion Blog Daily",
    "CAFC Patent Opinion Blog Catch-up",
    "PTAB Director Decisions Daily",
    "PTAB Precedential Decisions Daily",
    "ITC Section 337 Commission Daily"
  )
} | ForEach-Object {
  $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
  [PSCustomObject]@{
    TaskName  = $_.TaskName
    State     = $_.State
    NextRun   = $info.NextRunTime
  }
} | Sort-Object NextRun | Format-Table -AutoSize
