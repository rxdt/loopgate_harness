#!/bin/sh
# Ralph: hand PROMPT.md to a fresh-context agent and loop. The repo is the only memory.
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

MAX_ITERATIONS=2
MAX_MINUTES=20
case "${1:-}" in
    ''|*[!0-9]*) ;;
    *)
        MAX_ITERATIONS=$1
        shift
        case "${1:-}" in
            ''|*[!0-9]*) ;;
            *) MAX_MINUTES=$1; shift ;;
        esac
        ;;
esac

if [ "$#" -lt 1 ]; then
    echo "defaults: max_iterations=$MAX_ITERATIONS max_minutes_per_iteration=$MAX_MINUTES" >&2
    exit 2
fi

if [ "$MAX_ITERATIONS" -lt 1 ] || [ "$MAX_MINUTES" -lt 1 ]; then
    echo "ralph: max_iterations and max_minutes must be >= 1" >&2
    exit 2
fi

if command -v gtimeout > /dev/null 2>&1; then
    TIMEOUT=gtimeout
elif command -v timeout > /dev/null 2>&1; then
    TIMEOUT=timeout
else
    echo "ralph: need gtimeout or timeout" >&2
    exit 2
fi

i=1
while [ "$i" -le "$MAX_ITERATIONS" ]; do
    echo "ralph: iteration $i/$MAX_ITERATIONS" >&2
    PROMPT=$(cat PROMPT.md)   # read up front so a missing/unreadable prompt fails the loop (set -e)
    printf '%s\n\nRALPH_ITERATION=%s/%s\n' "$PROMPT" "$i" "$MAX_ITERATIONS" \
        | "$TIMEOUT" "$((MAX_MINUTES * 60))" "$@"
    i=$((i + 1))
done

echo "ralph: completed $MAX_ITERATIONS iteration(s)" >&2
