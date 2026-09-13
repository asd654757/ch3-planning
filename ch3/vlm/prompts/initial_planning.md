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
1. Only use skills from the registered closed set: "pick", "place", "push", or "press".
2. Only use object IDs from the closed-world list.
3. Use arm "left" or "right".
4. step_id starts at 1 and strictly increases by 1.
5. Action parameters are: a pick action must have target_id=null; a place action must include target_id (an object ID or "table"); a push action must include the destination target_id; a press action must have target_id=null.
6. Interpret goal facts as: on(object, target) means place; pushed_to(object, target) means push; pressed(object) means press.
7. A pick must be followed by its corresponding place for the same object and arm. Interleaved actions on two arms are allowed.
8. Do not pick or push while an arm is holding an object; do not press while an arm is holding an object.
9. Do not repeat a pick of the same object. If several objects match the same color/shape phrase, choose distinct object IDs so that every goal fact is satisfied exactly once.
10. Return the complete plan, never a partial prefix.
