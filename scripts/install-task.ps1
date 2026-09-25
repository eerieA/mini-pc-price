# Register the daily run with Task Scheduler (plan.md §9, Phase 2).
#
# NOT the normal way to run this any more. The daily poll and digest run in
# GitHub Actions (.github/workflows/daily.yml), because a desktop that sleeps
# loses observations that cannot be backfilled (§1). This is kept for the case
# where Actions is stalled or disabled and you want the local machine polling
# again -- if you do register it while Actions is also running, expect two
# digests on any day something moves.
#
#     powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1
#     powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1 -At 07:30
#
# Re-running replaces the existing task, so it doubles as the way to change the
# time. -WhatIf shows what would be registered without registering it.
#
# Runs as the current user, NOT with -RunLevel Highest and NOT as SYSTEM. This
# needs no privilege: it reads a database in the project directory and makes one
# outbound TLS connection. It also must run as the user who owns the .env file,
# since that is where the credentials come from.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$At = '08:00',
    [string]$TaskName = 'MiniPCDigest'
)

$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'scripts\run-daily.ps1'
if (-not (Test-Path $runner)) { throw "Not found: $runner" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Daily -At $At

# StartWhenAvailable is the setting that matters on a desktop: a machine asleep
# or off at 08:00 would otherwise simply skip the day, and a missed poll is an
# observation that cannot be backfilled (§1). This runs it at the next wake
# instead. Deliberately NOT WakeToRun -- waking a desktop to check prices is a
# worse trade than polling an hour late.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess($TaskName, "register daily at $At")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Poll mini-PC sources and email the ranked digest.' `
        -Force | Out-Null

    $task = Get-ScheduledTask -TaskName $TaskName
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    "Registered '$TaskName'"
    "  runs   : $At daily, as $($task.Principal.UserId)"
    "  next   : $($info.NextRunTime)"
    "  logs   : $(Join-Path $root 'logs')"
    ""
    "Test it now without waiting:  Start-ScheduledTask -TaskName $TaskName"
    "Remove it:                    Unregister-ScheduledTask -TaskName $TaskName"
}
