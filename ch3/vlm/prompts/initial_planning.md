Task instruction: {{instruction}}

Visible/closed-world scene object IDs:
{{objects}}

Goal facts (all must hold at the end):
{{goal}}

Return one ModelPlan JSON object only. Use exactly these fields:
{
  "actions": [
    {
      "step_id": 1,
      "skill": "pick",
      "object_id": "<object_id>",
      "target_id": null,
      "arm": "left"
    },
    {
      "step_id": 2,
      "skill": "place",
      "object_id": "<object_id>",
      "target_id": "<target_id>",
      "arm": "left"
    }
  ]
}

Before planning, first extract every object ID in the instruction and Goal facts.
Then compare those IDs with the visible/closed-world list.  If any required
object ID is absent, return exactly one refusal object instead:
{
  "status": "infeasible",
  "reason": "<short reason>"
}
Do not substitute another object and do not return the refusal object when every
required ID is visible.

Hard constraints:
1. Only use skill "pick" or "place".
2. Only use object IDs from the closed-world list.
3. Use arm "left" or "right".
4. step_id starts at 1 and strictly increases by 1.
5. A pick action must have target_id=null. A place action must include target_id (an object ID or "table").
6. Each pick must be immediately followed by the corresponding place for the same object and arm.
7. Do not pick a new object while an arm is holding one.
8. Each object may be picked at most once. If several objects match the same color/shape phrase, choose distinct object IDs so that every goal fact is satisfied exactly once.
9. Return the complete plan, never a partial prefix: every pick must be paired with its corresponding place in the same response.
