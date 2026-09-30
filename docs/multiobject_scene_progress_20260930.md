# Persistent multi-object feasibility

- Added two free-joint colored blocks and a physical placement platform to a
  temporary Sawyer scene; the native red puck remains. No installed asset or
  frozen benchmark was modified.
- Fixed nested include asset paths and explicit moving-body inertia. v1/v2
  failed before physics; v3 rendered with the old camera orientation. v4 is
  the current usable scene smoke. RGB and segmentation use the same flip.
- v4: one reset, 30 settling steps, 0.375 s simulated, 13 contacts; all four
  entities visible. This does not establish manipulation capability.
- Public RGB/instruction manifest is separate from evaluator-only geometry
  positions and segmentation. No simulator labels were sent to the VLM.
- Two single-case model requests were made. First: correct target relation,
  but wrong arm ID and 0-1000 boxes; second: malformed nine-number boxes.
  Both rejected; no execution. Do not describe this as successful grounding.
- The second run predates raw-response retention on parsing errors; its
  error is preserved but full response/cost was not retained. Future runs
  now retain both. Do not invent missing provenance.
- Next: isolate localization output from semantic state parsing, evaluate
  boxes against held-out masks, then implement evidence-based physical
  binding and per-object controllers in the same episode. Masks must remain
  evaluation-only, never a hidden binding shortcut.
