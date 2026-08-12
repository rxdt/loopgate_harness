#!/bin/sh
# Ralph: hand docs/PROMPT.md to a fresh-context agent and loop. The repo is the only memory.
# Keep Ralph Dumb: start the worker, give it the prompt, print a line, repeat. Nothing else.
# Setup (deps + git hooks) is `harness install`. The gate runs from the git hooks on commit.
# Want logs? Redirect this script: `harness/ralph.sh ... > run.log 2>&1`.
#
# Usage:
#   harness/ralph.sh [max_iterations] [max_minutes_per_iteration] <agent command...>
#  e.g.
#   harness/ralph.sh 10 20 claude -p --permission-mode acceptEdits
#   harness/ralph.sh 10 20 codex exec --json --sandbox workspace-write -
#
# ****      Motto: Keep Ralph Dumb.      ****
set -eu

# Mark loop commits so the gate (run by the git hooks) applies containment to the worker.
export RALPH_LOOP=1

MAX_ITERATIONS=$1
MAX_MINUTES=$2
TIMEOUT=$TIMEOUT
shift 2

i=1
while [ "$i" -le "$MAX_ITERATIONS" ]; do
    printf '{"type":"ralph","iteration":%s,"max_iterations":%s,"max_minutes":%s,"timestamp":"%s"}\n' \
    "$i" "$MAX_ITERATIONS" "$MAX_MINUTES" "$(date '+%Y-%m-%dT%H:%M')"

    printf '%s\n\nRALPH_ITERATION=%s/%s\n' "$RALPH_PROMPT" "$i" "$MAX_ITERATIONS" \
        | "$TIMEOUT" "$((MAX_MINUTES * 60))" "$@"
    i=$((i + 1))
done

printf '{"type":"ralph","completed":%s,"max_minutes":%s,"timestamp":"%s"}\n' "$((i - 1))" "$MAX_MINUTES" "$(date '+%Y-%m-%dT%H:%M')"
