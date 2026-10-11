# Fixed repeated-disturbance supplement

Two profiles, 50 paired seeds (10–59) each, three recovery methods, 300 sessions.
All use DeepSeek recovery and replay identical frozen Qwen initial plans. Shared
budgets: six logical planner calls, eight commands, twelve observations, normal
primitive budget 300 steps. No model prompt, success threshold, or frozen V3
file is changed. Feedback remains privileged structured simulator state.

- repeat_slip: physically open gripper after first two successful picks.
- repeat_slip_place_timeout: same two slips, then cap first place at ten steps.

Disturbances are finite; task is not deliberately made permanently impossible.
Both profiles and all seeds are retained regardless of results. Exact paired
statistics are exploratory (multiple comparisons); no claim of guaranteed
supervised advantage. Under the shared finite budget the second profile may
expose a common budget ceiling. Inspect realized disturbance counts; a scheduled
slip is not assumed realized. These are repeated controlled recovery stresses,
not novel multi-object tasks, natural failures, or visual/open-world evidence.
