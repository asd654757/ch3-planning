# Final attribution and baseline robustness experiment

## Scope and stopping rule

Keep the main V3 method frozen. No new simulator, policy weights, skills or
perception pipeline. Study LLM remaining-plan recovery under observed execution
state conflict, not general open-world control. Complete this one experiment,
analyze all cases and write the paper; do not select new tasks/prompts until a
preferred significance level is reached.

## Matched comparison (50 slip seeds, 150 sessions)

- STATE_FIRST_DIRECT: same observations and raw latest receipt as direct
  replanning; explicitly label the receipt as a historical event and restate
  the current planning basis. No diagnostic or iterative checker feedback.
- WITHOUT_CONFLICT: full V3 input except `state_conflicts`; immutable history,
  validator feedback, prompt, validation and budgets unchanged.
- SUPERVISED_RECOVERY: frozen full V3, rerun concurrently to control model-service
  variation instead of comparing only to historical successes.

All methods replay the exact initial output from formal-50 source, seeds 10–59.
Physical initial state must match the source before execution. Each recovery
runs within a single episode, with the same controlled gripper release. Input
source SHA-256 is journaled. Replayed initial calls count toward equal logical
budget, but actual API/token usage covers recovery only. This is an additional
attribution/robustness experiment on already evaluated seeds, not new held-out
performance evidence. Model services are not guaranteed deterministic by seed.

## Analysis

Primary: full versus without-conflict final success, with exact paired McNemar.
Secondary: full versus state-first direct, same paired test. Report raw and
Bonferroni-adjusted p values for two comparisons, Wilson intervals, first repair
acceptance, invalid candidate reasons, commands and recovery calls/tokens.
Keep unrealized disturbances, service failures and stopped cases visible; do not
filter them into an inflated success denominator. Analyzer refuses missing or
duplicate paired cases and journals without completion summaries.

## Interpretation fixed before running

- Full beats ablation: evidence for diagnostic's incremental contribution.
- Ablation matches full: attribute gains to the feedback bundle, not the conflict
  field alone. Efficiency differences may be reported, not invented.
- Strong direct matches full: original direct baseline is prompt-sensitive;
  narrow the comparative claim and report the stronger result prominently.
- Full fails: retain the failure analysis and frozen data; do not redefine scores.

## Run

With API key configured in the calling shell:

```bash
bash scripts/start_supervision_final.sh
```

Background execution; no live polling required. Completion requires
`[supervision-final] ALL_COMPLETED`. `/tmp/supervision_final_output` points to
`cases.jsonl`, `analysis.json` and launch commit. Real Qwen experiment has not
been started by the tool process without credentials. Scripted smoke is only
adapter validation and must not enter the paper's performance table.

## Model service substitution (2026-10-11)

The first final run produced only service errors for all recovery requests; its
zero successes are infrastructure-invalid, not method performance evidence.
A minimal request to `deepseek-v4.1-flash` at the configured DashScope endpoint
returned HTTP 200. The launcher now accepts `CH3_MODEL` and `CH3_BASE_URL`,
checks availability before launch, and aborts on model service errors.

```bash
CH3_MODEL=deepseek-v4.1-flash bash scripts/start_supervision_final.sh
```

All compared recovery methods use the same selected model. Initial plans are
replayed from the frozen Qwen journal and this cross-model lineage is recorded.
This is a recovery-only cross-model comparison, not a full DeepSeek planning
benchmark or a same-model causal ablation of the original Qwen results.
The feedback is structured simulator state, not camera images; this substitution
does not establish visual capability of the selected model. Frozen V3 source
files remain unchanged.

The first DeepSeek batch stopped without a traceback after four completed cases.
No live process or completion marker remained. Cause is unconfirmed; do not
interpret this partial journal as a completed benchmark. The launcher uses
`setsid` in addition to `nohup` to isolate the worker session and emits its exit
code. A replacement batch starts from the full fixed seed list in a new output
directory; the partial batch remains preserved and is not merged into results.
