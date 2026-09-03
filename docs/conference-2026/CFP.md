# AIE CODE Summit — sessionize.com/aiecode26 — closes Oct 11, 2026 23:59 PT

Submit all three. Speakers attend free; economy flight + 2 nights covered.
Every claim below is backed by the repos. Do not add numbers not in PROFILE.md.

## Speaker bio (≤ 100 words, reuse on all three)

Roxana del Toro is a San Francisco engineer who spent a decade shipping platform and
data infrastructure at Uber, Microsoft, and Meta, then a stint investing in
developer tools and frontier tech. She now builds AI applications with a small
team of interns and open-sources the tooling that keeps them honest: LoopGate, a
quality-gated loop harness for Claude Code, Codex, and Copilot, and Inference
Conference, a six-agent build-and-peer-review experiment featured on HackerNoon.
Her rule: human in the loop, build to survive first contact.
GitHub: rxdt · X: @roxdtvc · rxdt.dev

---

## 1 · Stage Talk (15–20 min)

**Title:** Agents can edit. Gates decide what lands.

**Abstract:**
Getting a coding agent to write code is solved. Getting it to land only code you'd
accept from a senior engineer is not. I run Claude Code, Codex, and Copilot in
autonomous loops on real projects, and the thing that made it work was not a better
prompt — it was a gate the agent cannot bypass.

This talk is the design of that gate, open-sourced as LoopGate. On every commit:
lint, format, and containment — forbidden paths the agent may not touch, enforced by
un-staging them, not by asking nicely. On every push: types, security scan (Semgrep),
dependency audit, complexity limits, property tests, 100% line coverage, and
mutation testing so the agent can't game coverage with assert-free tests. A 500-line
staged-diff cap kills the 2,000-line "refactor." No empty commits. Hard timeouts.
Fresh context every iteration, with the repo — plan, specs, status — as the only memory.

I'll show what the gate actually catches in a week of runs, the failure modes that
forced each rule (the agent that edited the gate config; the one that "fixed" a
failing test by deleting it), why interactive IDE sessions need the same gate as
loop workers, and the one rule I refuse to automate: only a human can `--no-verify`.

Attendees leave with a concrete checklist for gating their own agents, whichever CLI
they run.

**Why me:** I built and maintain the tool, run it daily on production projects with
a team that cannot review every agent commit, and have the run logs.

---

## 2 · Lightning (5–10 min)

**Title:** Nobody won: three agents built it, three agents judged it, all six were wrong the same way

**Abstract:**
I gave three independent agent sessions (Claude Opus and GPT) the same task: build a
spaCy-based recommender that maps a plain-English request to the right model out of
~13,000. Then I spun up three fresh agents with no shared context, told them to run
the code, argue, and explicitly forbade a comfortable consensus.

Results: the TF-IDF version confidently recommended an NSFW image generator for a
satellite-imagery query. The vector-retrieval version scored 77% on model-card text
and 18% on real user prompts. The judges' unanimous verdict — across model families —
was "nobody won, do it yourself." They converged anyway.

Five minutes on what that means for LLM-as-judge in your eval pipeline: shared blind
spots don't disappear when you diversify vendors, "run the code" beats "read the
code," and the domain-transfer cliff between docs and real prompts is where your
coding agent's confidence is least earned. Everything is public on GitHub.

**Why me:** I ran the experiment, published it (featured on HackerNoon), and have the
transcripts.

---

## 3 · Workshop (1–2 hr)

**Title:** Gate your coding agent in 90 minutes

**Abstract:**
Bring a repo. Leave with an autonomous coding-agent loop that cannot land code you
wouldn't accept.

Hands-on, in order: (1) `harness init` on your existing repo — gate config lands in
`pyproject.toml`, hooks install, nothing else changes. (2) Declare forbidden paths and
watch an agent try to edit them and get its changes un-staged. (3) Set the preflight
(commit) vs. full-gate (push) split and feel the latency trade-off. (4) Run a real
loop with your agent of choice — Claude Code, Codex, or Copilot — against a
two-paragraph plan and a spec, and read the run log to see what it thought. (5) Turn
on mutation testing and watch the agent's "100% coverage" tests get killed. (6) Put
your interactive IDE session under the same gate.

Prereqs: Python 3.10+, `uv`, one authenticated agent CLI, a repo you don't mind an
agent touching. Everything is MIT-licensed; no vendor account needed beyond the
agent you already use.

**Why me:** Author and maintainer of LoopGate; I onboard interns onto it, which is a
harsher test than any conference room.
