# Feedback format v10 exploratory development

Freeze before running: seeds 530..535, medium and hard, three methods, 36 episodes.
Direct vs original v9 diagnostic vs compact diagnostic. Identical initial request/output
within seed; fixed deepseek-v4.1-flash, thinking=false, max_tokens=4096, max two
noninitial requests. Cyclic method order. No search or programmatic action synthesis.
Compact format removes historical failed target repetition and full rejected candidate;
retains actionable constraint details, shared current state/goals/rules and validator.
Primary: independent terminal success. Cost: recovery token total and requests, excluding
shared initial output. Report full end-to-end costs separately if subsequently needed.
Latency exploratory only (sequential API calls, no latency significance claim).
All attempted episodes retained; no case selection by winner. These are six layouts
per condition, not 36 independent semantic tasks. Improvement over old format is not
proof of superiority over direct replanning. No confirmatory p claim from development.
