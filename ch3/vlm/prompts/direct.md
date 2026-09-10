Task instruction: {{instruction}}

Visible/closed-world scene object IDs:
{{objects}}

Goal facts (all must hold at the end):
{{goal}}

Write the task-level plan in concise free text, one action per line. Number
each line. Use exactly this grammar so a downstream parser can read it:
`N. pick <object_id> with <left|right> arm`
`N. place <object_id> on <target_id> with <left|right> arm`

Do not write explanations, JSON, code blocks, or extra constraints. Use only
object IDs from the visible list. Place target may be an object ID or "table".

First extract every object ID in the instruction and Goal facts. If any such ID
is absent from the visible list, write exactly one line instead:
`INFEASIBLE: <short reason>`
Do not use this refusal when all required IDs are visible.
