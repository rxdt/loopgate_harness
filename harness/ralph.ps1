# Ralph (Windows twin of ralph.sh). Hand docs/PROMPT.md to a fresh-context agent and loop.
# Keep Ralph Dumb: start the worker, give it the prompt, print a line, repeat. Nothing else.
# Windows has no POSIX `timeout`, so this uses Process.WaitForExit + taskkill /T.
#
# Usage:
#   powershell.exe -File ralph.ps1 <max_iterations> <max_minutes_per_iteration> <agent command...>

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# Mark loop commits so the gate (run by the git hooks) applies containment to the worker.
$env:RALPH_LOOP = "1"

function ConvertTo-WindowsArgument([string]$argument) {
    $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
    return '"' + $escaped + [regex]::Match($argument, '(\\*)$').Groups[1].Value + '"'
}

function Write-RalphEvent([System.Collections.IDictionary]$payload) {
    [Console]::Out.WriteLine(($payload | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}

if ($args.Count -lt 3) {
    [Console]::Error.WriteLine(
        "Usage: ralph.ps1 <max_iterations> <max_minutes_per_iteration> <agent command...>"
    )
    exit 2
}

$maxIterations = [int]$args[0]
$maxMinutes = [double]$args[1]
$worker = @($args | Select-Object -Skip 2)
$timeoutMilliseconds = [int][Math]::Ceiling($maxMinutes * 60 * 1000)

$iteration = 1
while ($iteration -le $maxIterations) {
    Write-RalphEvent ([ordered]@{
        type = "ralph"
        iteration = $iteration
        max_iterations = $maxIterations
        max_minutes = $maxMinutes
        timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm")
    })

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $worker[0]
    if ($worker.Count -gt 1) {
        $startInfo.Arguments = (($worker[1..($worker.Count - 1)] | ForEach-Object {
            ConvertTo-WindowsArgument $_
        }) -join ' ')
    }
    $startInfo.RedirectStandardInput = $true
    $startInfo.UseShellExecute = $false

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $prompt = "$($env:RALPH_PROMPT)`n`nRALPH_ITERATION=$iteration/$maxIterations`n"
    $process.StandardInput.Write($prompt)
    $process.StandardInput.Close()

    if (-not $process.WaitForExit($timeoutMilliseconds)) {
        taskkill.exe /F /T /PID $process.Id | Out-Null
        $process.WaitForExit()
        $process.Dispose()
        exit 124
    }

    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0) {
        exit $exitCode
    }

    $iteration += 1
}

Write-RalphEvent ([ordered]@{
    type = "ralph"
    completed = $iteration - 1
    max_minutes = $maxMinutes
    timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm")
})
