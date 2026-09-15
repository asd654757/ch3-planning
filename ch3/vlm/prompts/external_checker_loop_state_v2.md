Checker-loop repair with structured state, goal skeleton, and deterministic feedback

Task instruction:
{{instruction}}

Closed-world object IDs:
{{objects}}

Goal facts (all must hold at the end):
{{goal}}

Current structured state:
{{current_state}}

Goal action skeleton:
{{goal_action_skeleton}}

Remaining goal facts:
{{remaining_goal_facts}}

Required transports:
{{required_transports}}

Release actions:
{{release_actions}}

Failed plan:
{{failed_plan}}

External deterministic checker feedback:
{{checker_feedback}}

Regenerate the complete plan so it satisfies the task and checker. Complete
every action pattern listed in goal_action_skeleton. If the task is
infeasible, return exactly:
{"status": "infeasible", "reason": "<short reason>"}

Otherwise return the complete plan as one ModelPlan JSON object.
Use only skills "pick", "place", "push", or "press". A pick or press has
target_id=null; a place or push has target_id.
{"actions": [{"step_id": 1, "skill": "pick", "object_id": "<object_id>", "target_id": null, "arm": "left"}]}

Output only one JSON object. Do not add commentary or Markdown.
