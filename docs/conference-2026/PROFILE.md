# Profile — Roxana "rox" del Toro (built from public sources, 2026-09-03)

Every line below has a source. Nothing is inferred. Fix anything wrong before it goes on a form.

| Field | Value | Source |
|---|---|---|
| Name | Roxana del Toro ("rox") | github.com/rxdt |
| Email | rxdeltoro@gmail.com | git log |
| City | San Francisco, CA | github.com/rxdt |
| Site | rxdt.dev | github.com/rxdt |
| GitHub | github.com/rxdt — 67 public repos, 41 followers, since 2014 | github.com/rxdt |
| X | @roxdtvc | github.com/rxdt |
| LinkedIn | linkedin.com/in/roxdt | rxdt.dev |
| Bio (own words) | "After a long time at FAANGs and a stint in VC I now build apps with my interns" | github.com/rxdt |
| Engineering | Uber (reliability-focused platform), Microsoft (data infrastructure), Meta (Meta Quest launch; developer strategy + fintech partnerships) | LinkedIn/Signal summary |
| Investing | The Council; prev. VU Venture Partners, Cartography Cap | signal.nfx.com, LinkedIn |
| **Missing — company name + title for forms** | rxdt.dev says "builds AI applications"; no legal entity or title found. **Default: "Independent / rxdt.dev", title "Engineer".** Override if wrong. | — |

## Shipped work (the material for CFP + forms)

**LoopGate** — github.com/rxdt/loopgate_harness · PyPI `loopgate` 0.1.2 · MIT · GitHub template repo
Coding-agent loop harness: agents (Claude Code, Codex, Copilot, Agy) edit; a gate decides what lands. Tiered gate: preflight on commit (ruff lint/format + containment), full gate on push (pyright, pylint, pydoclint, complexipy, semgrep, pip-audit, pytest @ 100% coverage, hypothesis, mutmut). Forbidden-path containment that self-heals by un-staging; 500-LOC staged-diff cap; no empty commits; per-iteration timeouts; fresh-context iterations with repo-as-memory (plan → specs → status). `configure-agents` puts interactive IDE/terminal sessions under the same gates as loop workers. Only humans can `--no-verify`. Mutation score on itself: 83.6%. 20★ / 12 forks / 53 commits since 2026-06-21. Also `loopgate_js` (TS/Vite).

**Inference Conference** — github.com/rxdt/inference_conference · featured on HackerNoon
Six independent agent sessions (Claude Opus + GPT): three built spaCy intent→model recommenders over ~13k models; three fresh agents peer-reviewed by running the code and debating for hours. V1 (TF-IDF ensemble) confidently recommended an NSFW image generator for a satellite-imagery query. V2's classifier: 77% on model-card text → 18% on real user prompts. Panel verdict: "Nobody won." Cross-family judges converged on consensus despite being told not to → shared blind spots.

**AI Deployment Calculator** — VRAM/GPU estimator for LLM, embedding, vision, diffusion workloads. Web app.

## Routes this profile kills or opens
- Media pass: **no newsletter/podcast/blog with an audience** found. rxdt.dev is a portfolio. Only credit: HackerNoon feature (see LOG). Weak; submit only as "Web / Influencer" with the HackerNoon link, expect a no.
- CODE Summit CFP: **strong fit.** Two real coding-agent stories with numbers. See CFP.md.
- Startup Showdown: no company/funding found. Low fit. Draft exists in OUTREACH.md if there's a company I don't know about.
- Sponsor comps: need your paid-vendor list. Not public.
