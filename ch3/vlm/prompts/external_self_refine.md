Self-refine mode (no deterministic validator feedback)

Task instruction:
{{instruction}}

Visible/closed-world scene object IDs:
{{objects}}

Goal facts (all must hold at the end):
{{goal}}

Current state:
{{current_state}}

Current plan to review:
{{current_plan}}

Review the current plan yourself against the instruction, goal, visible object IDs,
state transitions, and action schema. If the task is infeasible, return exactly:
{"status": "infeasible", "reason": "<short reason>"}

Otherwise return the complete corrected plan as one ModelPlan JSON object:
{"actions": [{"step_id": 1, "skill": "pick", "object_id": "<object_id>", "target_id": null, "arm": "left"}]}

Output only one JSON object. Do not add commentary or Markdown.
