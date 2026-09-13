Repair mode: {{mode}}

Task instruction: {{instruction}}

Visible/closed-world scene object IDs:
{{objects}}

Goal facts (all must hold at the end):
{{goal}}

Current task data:
{{task_data}}

{{output_requirement}}

Return one ModelPlan JSON object only:
{
  "actions": [
    {
      "step_id": 1,
      "skill": "pick",
      "object_id": "<object_id>",
      "target_id": null,
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
For R2, return only the replacement suffix requested by the output requirement.

Hard constraints:
1. Only use skills from the registered closed set: "pick", "place", "push", or "press".
2. Only use object IDs from the closed-world list.
3. Use arm "left" or "right".
4. Action parameters are: a pick action must have target_id=null; a place action must include target_id (an object ID or "table"); a push action must include the destination target_id; a press action must have target_id=null.
5. Interpret goal facts as: on(object, target) means place; pushed_to(object, target) means push; pressed(object) means press.
6. Do not pick, push, or press while an arm is holding an object.
7. Do not repeat a pick of the same object.
8. For R0/R1, return the complete plan, never a partial prefix. For R2 and R1_FROM_STATE, return only the requested suffix. For R1_FROM_STATE, if `remaining_goal_facts` is empty, return exactly `{"actions": []}`.
