# LoopGate Glossary

Short definitions of common LoopGate terms.

- harness: the LoopGate tool that runs agents, manages loops, and checks changes.
- loop: one cycle of work where the worker reads instructions, works on a spec, makes changes, runs checks, and records progress.
- worker: the coding agent that does the work, such as Claude, Codex, or Copilot.
- gate: the checks that decide whether changes can be accepted. LoopGate runs these checks locally and in CI.
- preflight: the quick checks that run before the full gate to catch common problems early.
- prompt: the instructions in `docs/PROMPT.md` that tell the worker what to do in each loop.
- spec: a file in `docs/specs/` that describes what needs to be built.
- Ralph: LoopGate's loop runner. It starts the worker, gives it the prompt, and runs the workflow for each iteration.