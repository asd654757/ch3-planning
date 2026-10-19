# v9 diagnostic ablation: development protocol

This is exploratory development, not a confirmatory test. No superiority is assumed.
Model: deepseek-v4.1-flash; enable_thinking=false; max_tokens=4096.
Conditions: medium and hard, seeds 520..525 inclusive, paired methods; 24 episodes.
Same current observation, goals, rules, controller, acceptance gate and 2 noninitial requests.
Difference: explicit observed-execution diagnostic and candidate violation feedback.
Initial proposal shared within seed, method order counterbalanced by seed parity.
Primary outcome: independent terminal task success; report all failures, including API/parse.
Secondary: recovery request tokens/latency (exclude shared initial); parsing/constraint rejections.
No scenario exclusion based on wins, no model switching mid-run, no truth-fed planning.
If tied, report no observed advantage. Small-sample differences require independent testing.
Known scope: restricted two-object fixture, controlled place offset; hard adds goal update.
