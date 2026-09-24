# Register (or replace) the Windows scheduled task that runs scripts\matchday.ps1.
#
# Twice a day, 11:00 and 17:00, every day: a run with no open matchday is a no-op, and two
# passes pick up late probabili formazioni (the platform's `percent` moves until kickoff).
# Runs as the current user, only when logged on, so it can reach Docker Desktop.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_matchday_task.ps1
#   Unregister-ScheduledTask -TaskName fantabot-matchday -Confirm:$false   # to remove it

$script = Join-Path $PSScriptRoot "matchday.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At "11:00"
    New-ScheduledTaskTrigger -Daily -At "17:00"
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "fantabot-matchday" -Action $action -Trigger $triggers `
    -Settings $settings -Description "fantabot: submit the formazione for every open lega" -Force
