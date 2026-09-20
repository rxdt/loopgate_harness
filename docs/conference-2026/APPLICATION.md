# AIE CODE Summit 2026 — attendee application (DRAFT, NOT SUBMITTED)

Route: official MCP `https://ai.engineer/mcp` → `code_2026_submit_application` (organizer-sanctioned agent route). Rate limit 10/hr.
Validated against the payloadSchema returned by `code_2026_get_application` on 2026-09-20. Server-side validate accepted the field set; the final trimmed text (1977 chars) was checked locally against the same schema after the sandbox blocked a repeat validate call.
Applications opened between 2026-09-10 and 2026-09-20. **Early review deadline Sep 15 has passed.** Final deadline Oct 11, 23:59 PT. Reviews are rolling — submit now.

Organizer rule (server instructions): applicant must approve the complete answers before `applicantApproved: true`. **Your one-word approval unblocks submission.**

## Answers
**firstName**

> Roxana

**lastName**

> del Toro

**email**

> rxdeltoro@gmail.com

**company**

> Independent (rxdt.dev)

**jobTitle**

> Engineer

**location**

> San Francisco, USA

**github**

> https://github.com/rxdt

**social**

> https://x.com/roxdtvc

**linkedin**

> https://www.linkedin.com/in/roxdt/

**website**

> https://rxdt.dev

**roles**

- AI Engineer
- Senior/Staff+ Engineer
- Indie Hacker

**topics**

- Coding agents & workflows
- Evaluations & code review
- Security & reliability

**achievements**

> LoopGate (github.com/rxdt/loopgate_harness; pip install loopgate): open-source, quality-gated loop harness for Claude Code, Codex, and Copilot. Agents can edit; a gate decides what lands. Preflight on every commit: ruff lint/format plus containment (forbidden paths the agent may not touch, enforced by un-staging them). Full gate on every push: pyright, pylint, pydoclint, complexipy, Semgrep, pip-audit, pytest at 100% coverage, Hypothesis, mutmut mutation testing. 500-line staged-diff cap, no empty commits, per-iteration timeouts, fresh context each iteration with the repo (plan, specs, status) as the only memory. `harness configure-agents` puts interactive IDE/terminal sessions under the same gates as loop workers; only a human can --no-verify. I designed and maintain it and run it daily with a small team of interns. Mutation score on its own suite: 83.6%.
>
> What I learned: the prompt is not the control surface; the gate is. Each rule maps to a way an agent can satisfy a metric without doing the work: 100% coverage with assert-free tests (so mutation testing), a green run by editing the check config (so self-healing forbidden paths), a 2,000-line 'cleanup' (so the diff cap), an empty commit to end the loop (so it is blocked). Mutation testing is the one check an agent cannot game.
>
> Second project, Inference Conference (github.com/rxdt/inference_conference; HackerNoon, July 2026): three independent agent sessions each built a spaCy intent-to-model recommender over ~13k models; three fresh agents peer-reviewed by running the code. The TF-IDF version recommended an NSFW image generator for a satellite-imagery query; the vector version scored 77% on model-card text, 18% on real prompts. Judges across model families converged on 'nobody won' despite being told to avoid consensus: diversifying vendors does not remove shared blind spots.
>
> Background: a decade of platform and data infrastructure at Uber, Microsoft, and Meta, then VC in developer tools.

**bio**

> Roxana del Toro is a San Francisco engineer who spent a decade shipping platform and data infrastructure at Uber, Microsoft, and Meta, then a stint investing in developer tools and frontier tech. She now builds AI applications with a small team of interns and open-sources the tooling that keeps them honest: LoopGate, a quality-gated loop harness for Claude Code, Codex, and Copilot, and Inference Conference, a six-agent build-and-peer-review experiment featured on HackerNoon.
