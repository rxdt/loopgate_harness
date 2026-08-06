#!/usr/bin/env bash
# Answer one question: should containment be ON for this git invocation?
#
# WHY. gate.py switches every containment check on RALPH_LOOP, and ralph.sh is what exports it. Lose
# that variable any way -- an agent unsets it, a subshell starts with a cleared environment, a
# wrapper re-execs, an IDE agent that ralph.sh never launched -- and forbidden-path ejection, the
# banned-pattern scan and the preferences walk all switch off silently. The gate fails open.
#
# THE DESIGN IS FAIL-CLOSED. Containment is assumed ON. The only thing that turns it off is positive
# evidence of a human: an interactive terminal, with no agent fingerprint on it. Everything else --
# CLI agent, IDE agent, CI, a script, a cron job -- is treated as an agent. This is deliberate. An
# earlier version of this script tried to enumerate agents and switched containment on only for a
# name it recognised, which meant every agent it had not heard of walked straight through.
#
# THREE SIGNALS, checked in order. Any one of them means "agent".
#
#   1. ENVIRONMENT MARKER. Cheap and exact when present, absent for most IDE agents.
#   2. PROCESS ANCESTRY. Catches a CLI agent that unset its own variables, because a process cannot
#      remove itself from its own process tree. Useless for an IDE agent: VS Code, Cursor and Zed
#      run the agent inside the extension host, so the ancestor chain is `node`/`code`, never an
#      agent binary.
#   3. NO CONTROLLING TERMINAL. The one signal that generalises. A human running `git commit` types
#      it at a terminal, which means a pty -- including the IDE's own integrated terminal. An agent
#      spawning git through child_process/subprocess gets no pty, whatever IDE it lives in.
#
# KNOWN FALSE POSITIVE, and it is the safe direction: committing from a GUI button (VS Code's Source
# Control panel, GitKraken) has no pty either, so a human doing that is treated as an agent and gets
# containment. The cost is an unstaged forbidden file and a message saying so. `--no-verify` remains
# the documented human escape hatch. The opposite mistake -- silently letting an agent through --
# leaves no trace at all.
#
# Run `harness/agent_detect.sh --explain` inside your own editor, agent or terminal to see exactly
# which signals fire there. That output is how you extend the two lists below for a tool not yet
# named in them.
#
# Exit 0: treat as agent, containment ON. Exit 1: human at an interactive terminal.

set -uo pipefail

# Marker variables an agent runtime exports for itself. NOT credentials like ANTHROPIC_API_KEY: a
# human exports those too, and treating them as proof of an agent punishes the wrong person.
# Confirmed present in this repo's own runs: RALPH_LOOP (ralph.sh), CLAUDECODE and
# CLAUDE_CODE_ENTRYPOINT (observed), and the three CODEX_* names (pyproject.toml clears them when
# launching codex). The rest are reported names that have NOT been confirmed here -- they cost
# nothing when absent, and `--explain` in your own tool is how you replace a guess with a fact.
AGENT_ENV_VARS="
RALPH_LOOP
CLAUDECODE
CLAUDE_CODE_ENTRYPOINT
CLAUDE_CODE_SIMPLE
CODEX_THREAD_ID
CODEX_CONVERSATION_ID
CODEX_SESSION_ID
CURSOR_AGENT
GITHUB_COPILOT_AGENT
AIDER_CHAT
GEMINI_CLI
"

# Binaries that mean an agent is driving when one appears in the parent chain. Editor names are
# deliberately absent: `code` in the ancestry means an IDE, and an IDE holds humans and agents both.
# Signal 3 is what separates them.
AGENT_PROCESS_NAMES="claude codex copilot agy aider"

explain=""
if [ "${1:-}" = "--explain" ]; then
    explain="yes"
fi

# ---------------------------------------------------------------- signal 1: environment markers
marker_found=""
for variable in $AGENT_ENV_VARS; do
    if [ -n "${!variable:-}" ]; then
        marker_found=$variable
        break
    fi
done

# ------------------------------------------------------------------- signal 2: process ancestry
# `ps -o ppid=,comm=` is the portable spelling; Linux and macOS agree on it. The walk is
# depth-bounded so a malformed or circular chain can never hang a commit.
ancestor_found=""
chain=""
inspected=$PPID
for _ in $(seq 1 12); do
    case "$inspected" in
        '' | 0 | 1 | *[!0-9]*) break ;;
    esac
    line=$(ps -o ppid=,comm= -p "$inspected" 2>/dev/null) || break
    [ -n "$line" ] || break
    parent=$(echo "$line" | awk '{print $1}')
    command_name=$(echo "$line" | awk '{print $2}')
    command_name=${command_name##*/}   # strip any path, keep the bare binary name
    chain="$chain $command_name"
    for candidate in $AGENT_PROCESS_NAMES; do
        case "$command_name" in
            "$candidate" | "$candidate".* | "$candidate"-*)
                [ -n "$ancestor_found" ] || ancestor_found=$command_name
                ;;
        esac
    done
    inspected=$parent
done

# ------------------------------------------------------------ signal 3: no controlling terminal
# A human typed this at a terminal, so stdin is a pty. Anything that spawned git programmatically
# has no terminal on stdin, whether it lives in a shell, an editor or CI.
interactive=""
if [ -t 0 ]; then
    interactive="yes"
fi

if [ -n "$explain" ]; then
    echo "environment marker : ${marker_found:-none of the known names are set}"
    echo "agent in ancestry  : ${ancestor_found:-none}"
    echo "ancestor chain     :${chain:- (empty)}"
    echo "stdin is a terminal: ${interactive:-no}"
    echo "controlling tty    : $(ps -o tty= -p $$ 2>/dev/null | tr -d ' ')"
    if [ -n "$marker_found" ] || [ -n "$ancestor_found" ] || [ -z "$interactive" ]; then
        echo "verdict            : AGENT (containment on)"
    else
        echo "verdict            : human (containment off)"
    fi
fi

if [ -n "$marker_found" ]; then
    [ -n "$explain" ] || echo "agent detected: environment marker $marker_found"
    exit 0
fi
if [ -n "$ancestor_found" ]; then
    [ -n "$explain" ] || echo "agent detected: process ancestor $ancestor_found"
    exit 0
fi
if [ -z "$interactive" ]; then
    [ -n "$explain" ] || echo "agent assumed: no controlling terminal (nothing interactive is attached)"
    exit 0
fi

exit 1
