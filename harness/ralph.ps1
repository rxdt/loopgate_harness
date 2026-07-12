# Ralph (Windows twin of ralph.sh). Hand docs/PROMPT.md to a fresh-context agent and loop.
# Keep Ralph Dumb: start the worker, give it the prompt, print a line, repeat. Nothing else.
# Windows has no POSIX `timeout`, so this uses Wait-Process + taskkill /T to bound each iteration.
#
# Usage: powershell -File ralph.ps1 [max_iterations] [max_minutes_per_iteration] <agent command...>
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)

$ErrorActionPreference = "Stop"
$env:RALPH_LOOP = "1"   # mark loop commits so the gate applies containment to the worker

$maxIterations = 2
$maxMinutes = 20
$rest = @($Args)
if ($rest.Count -gt 0 -and $rest[0] -match '^\d+$') { $maxIterations = [int]$rest[0]; $rest = $rest[1..($rest.Count - 1)] }
if ($rest.Count -gt 0 -and $rest[0] -match '^\d+$') { $maxMinutes = [int]$rest[0]; $rest = $rest[1..($rest.Count - 1)] }

if ($rest.Count -lt 1) {
    Write-Error "defaults: max_iterations=$maxIterations max_minutes_per_iteration=$maxMinutes"; exit 2
}
if ($maxIterations -lt 1 -or $maxMinutes -lt 1) {
    Write-Error "ralph: max_iterations and max_minutes must be >= 1"; exit 2
}

for ($i = 1; $i -le $maxIterations; $i++) {
    [Console]::Error.WriteLine("ralph: iteration $i/$maxIterations")
    $stdin = "$($env:RALPH_PROMPT)`n`nRALPH_ITERATION=$i/$maxIterations`n"
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $rest[0]
    foreach ($a in $rest[1..($rest.Count - 1)]) { $psi.ArgumentList.Add($a) }
    $psi.RedirectStandardInput = $true
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    # feed the prompt on stdin, then bound the run; taskkill /T kills the agent AND its children
    $proc.StandardInput.Write($stdin); $proc.StandardInput.Close()
    # Bound the run. Like ralph.sh's `set -e` + timeout: a timeout or a nonzero worker exit stops the
    # loop and propagates failure, so `harness run` never reports success for a failed iteration.
    if (-not $proc.WaitForExit($maxMinutes * 60 * 1000)) {
        taskkill.exe /F /T /PID $proc.Id | Out-Null
        exit 124   # match GNU timeout's exit code
    }
    if ($proc.ExitCode -ne 0) {
        exit $proc.ExitCode
    }
}

[Console]::Error.WriteLine("ralph: completed $maxIterations iteration(s)")
