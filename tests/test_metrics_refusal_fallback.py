from ch3.metrics.metrics import compute_b1_metrics, refusal_source


def _record(raw: str, **overrides) -> dict:
    base = {
        "record_type": "initial_shared_plan",
        "response_protocol": "infeasible",
        "valid": False,
        "goal_satisfied": False,
        "pass_but_wrong": False,
        "raw_vlm_output": raw,
    }
    base.update(overrides)
    return base


def test_refusal_source_recomputes_pre_v8_records() -> None:
    model = _record('{"status":"infeasible","reason":"missing object"}')
    guard = _record('{"actions":[{"step_id":1,"skill":"pick","object_id":"x","arm":"right"}]}')
    explicit_guard = _record(
        "normal",
        infeasible_source="deterministic_guard",
    )
    assert refusal_source(model) == "model_refusal"
    assert refusal_source(guard) == "deterministic_guard"
    assert refusal_source(explicit_guard) == "deterministic_guard"


def test_b1_metrics_use_fallback_for_old_collections() -> None:
    records = [
        _record('{"status":"infeasible","reason":"missing"}'),
        _record("INFEASIBLE: missing"),
        _record("ordinary plan"),
    ]
    result = compute_b1_metrics(records)
    assert result["infeasible_refusals"] == 3
    assert result["model_only_refusals"] == 2
    assert result["deterministic_guard_refusals"] == 1
