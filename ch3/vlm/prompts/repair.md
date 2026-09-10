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

Hard constraints:
1. Only use skill "pick" or "place".
2. Only use object IDs from the closed-world list.
3. Use arm "left" or "right".
4. A pick action must have target_id=null. A place action must include target_id.
5. Each pick must be immediately followed by the corresponding place for the same object and arm.
6. Do not pick a new object while an arm is holding one.
7. Each object may be picked at most once. Do not repeat a pick of the same object.
