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
