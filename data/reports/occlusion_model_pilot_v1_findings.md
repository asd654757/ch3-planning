# Model acquisition pilot v1 (seeds 750–754)

All ten episodes completed. Direct: 5/5, ten noninitial calls, 5025 noninitial
tokens. Compact: 4/5, nine noninitial calls, 5247 noninitial tokens. All ten had
an actual acquisition request; nine acquired the registered alternate view.

Compact seed 753 returned a prose scene description rather than the decision JSON;
JSONDecodeError safely stopped execution. This is an output-protocol failure,
not evidence that compact chose stop or made an inferior physical recovery decision.
Do not exclude it from all-cause success. Preserve v1 negative outcome.

v2 change applies equally to BOTH methods: response_format json_object for observation
decisions. Same two noninitial calls; no extra parse retry budget. Planning output
protocol unchanged. Fresh fixed development seeds 760–764, ten episodes. Version
change is not pooled into v1; all failures reported. Pilot launcher now uses v2 seeds.

Remaining limitation: known initial fixture goals/state, one camera-selection option,
and a required observe/stop choice. A successful tie would establish bounded acquisition
compatibility, not historical reasoning advantage or general open-world necessity.
