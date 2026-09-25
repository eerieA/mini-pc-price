# The daily run, as Task Scheduler invokes it (plan.md §9, Phase 2).
#
# Poll, then digest, appending both to one dated log. Run it by hand exactly as
# the scheduler does:
#
#     powershell -ExecutionPolicy Bypass -File scripts\run-daily.ps1
#
# Two decisions worth knowing before changing anything here:
#
# The digest is sent EVEN IF THE POLL FAILED, and says so. The alternative --
# skip the digest on a failed poll -- produces silence, and silence is exactly
# what a working day with no new deals looks like (§8). A digest carrying
# yesterday's prices with a visible warning is honest; no digest at all is not.
#
# Nothing here reads a credential. digest.py loads the git-ignored .env itself,
# which is why this script does not need to pass secrets through a scheduler
# argument, where they would be readable in the task definition by anyone with
# the box.

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("daily-" + (Get-Date -Format 'yyyy-MM') + ".log")

function Write-Log($message) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    Add-Content -Path $log -Value $line -Encoding utf8
}

# Monthly log, not per-run files: a year of daily runs is 12 readable files
# rather than 365, and the failure being looked for is a run that stopped
# happening -- which is visible as a gap in one file and invisible across many.
Write-Log "--- run start ---"

Push-Location $root
try {
    $pollOutput = & python src\poll.py 2>&1
    $pollOk = ($LASTEXITCODE -eq 0)
    foreach ($line in $pollOutput) { Write-Log "poll | $line" }
    if (-not $pollOk) { Write-Log "poll | FAILED (exit $LASTEXITCODE) - digest will use the last successful poll" }

    $digestOutput = & python src\digest.py 2>&1
    $digestOk = ($LASTEXITCODE -eq 0)
    foreach ($line in $digestOutput) { Write-Log "digest | $line" }

    if ($digestOk) {
        Write-Log "--- run ok (poll $(if ($pollOk) {'ok'} else {'FAILED'})) ---"
        exit 0
    }
    Write-Log "--- run FAILED: digest did not send ---"
    exit 1
}
finally {
    Pop-Location
}
