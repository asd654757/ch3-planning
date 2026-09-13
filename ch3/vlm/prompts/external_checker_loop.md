Checker-loop repair mode (full-plan regeneration)

Task instruction:
{{instruction}}

Visible/closed-world scene object IDs:
{{objects}}

Goal facts (all must hold at the end):
{{goal}}

Current state:
{{current_state}}

Failed plan:
{{failed_plan}}

External deterministic checker feedback:
{{checker_feedback}}

Regenerate the complete plan so it satisfies the task and checker. This mode has
no prefix lock; you may revise any step. If the task is infeasible, return exactly:
{"status": "infeasible", "reason": "<short reason>"}

Otherwise return the complete plan as one ModelPlan JSON object.
Use only skills "pick", "place", "push", or "press". A pick or press has
target_id=null; a place or push has target_id.
{"actions": [{"step_id": 1, "skill": "pick", "object_id": "<object_id>", "target_id": null, "arm": "left"}]}

Output only one JSON object. Do not add commentary or Markdown.
