import json
from pathlib import Path
import subprocess
import sys

import pytest

from ch3.supervision import Evidence, Truth
from ch3.supervision import ClientPlanner
from ch3.supervision.demo import build_session
from ch3.supervision.runtime import JsonlJournal, Session, run_session
from ch3.vlm.client import DashScopeVLMClient


def test_mock_session_records_repair_and_post_execution_confirmation():
    session = build_session()
    events = []
    result = run_session(session, emit=events.append)
    assert result["confirmed_complete"]
    assert result["evidence_kind"] == "mock_integration_only"
    assert result["observations"] == 4
    assert result["model_calls"] == 2
    assert result["commands"] == 3
    executions = [e for e in events if e["event"] == "execution"]
    assert [e["action"]["skill"] for e in executions] == ["pick", "pick", "place"]
    assert len({e["command_id"] for e in executions}) == 3
    assert events[-3]["event"] == "observation"
    assert events[-1]["event"] == "session_summary"


def test_observation_budget_is_not_reported_as_success():
    result = run_session(build_session(), max_observations=1)
    assert not result["confirmed_complete"]
    assert result["stop_reason"] == "observation_budget_exhausted"
    assert result["commands"] == 1


def test_missing_occupancy_never_calls_model_or_backend():
    session = build_session()
    original = session.observer.read

    def incomplete():
        obs = original()
        obs.occupancy = {}
        return obs

    session.observer.read = incomplete
    result = run_session(session, max_observations=3)
    assert result["observations"] == 3
    assert result["model_calls"] == result["commands"] == 0


def test_state_completion_without_evidence_requests_confirmation_not_motion():
    session = build_session()
    session.backend.state.at["red_cube_0"] = "tray_0"
    original = session.observer.read

    def ambiguous():
        obs = original()
        obs.goal_evidence = {"on(red_cube_0, tray_0)": Evidence(Truth.UNKNOWN, "mock_observer")}
        return obs

    session.observer.read = ambiguous
    result = run_session(session, max_observations=2)
    assert not result["confirmed_complete"]
    assert result["model_calls"] == result["commands"] == 0


def test_observer_error_stops_without_leaking_exception_details():
    session = build_session()

    def broken():
        raise RuntimeError("secret_service_response")

    session.observer.read = broken
    events = []
    result = run_session(session, emit=events.append)
    assert result["stop_reason"] == "observation_error"
    assert result["error_type"] == "RuntimeError"
    assert "secret_service_response" not in json.dumps(events)
    assert result["commands"] == 0


def test_stale_observation_stops_instead_of_reexecuting():
    session = build_session()
    obs = session.observer.read()
    session.observer.read = lambda: obs
    result = run_session(session)
    assert result["stop_reason"] == "observation_error"
    assert result["commands"] == 1


def test_rejected_plans_stop_with_logged_attempts():
    session = build_session()
    session.supervisor.planner.generate = lambda request, images: '{"actions":[]}'
    events = []
    result = run_session(session, emit=events.append)
    assert result["stop_reason"] == "safe_stop"
    assert result["commands"] == 0
    assert len([e for e in events if e["event"] == "model_attempt"]) == 2


def test_execution_budget_stop_is_reported():
    session = build_session()
    session.supervisor.max_commands = 1
    result = run_session(session)
    assert result["stop_reason"] == "dispatch_error"
    assert result["supervisor_status"] == "safe_stop"
    assert result["commands"] == 1


def test_journal_preserves_existing_output(tmp_path):
    path = tmp_path / "events.jsonl"
    with JsonlJournal(path) as journal:
        run_session(build_session(), emit=journal.emit)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["event"] == "session_start"
    assert rows[-1]["confirmed_complete"]
    with pytest.raises(FileExistsError):
        JsonlJournal(path)


def test_cli_works_without_pythonpath_and_refuses_overwrite(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_supervision.py"
    path = tmp_path / "journal.jsonl"
    command = [sys.executable, str(script), "--output", str(path)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["confirmed_complete"]
    before = path.read_bytes()
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert path.read_bytes() == before


def test_unknown_backend_receipt_is_observed_before_repair():
    session = build_session()
    original = session.backend.execute

    def uncertain(*args, **kwargs):
        if session.backend.count == 0:
            session.backend.count += 1
            raise TimeoutError("private backend details")
        return original(*args, **kwargs)

    session.backend.execute = uncertain
    events = []
    result = run_session(session, emit=events.append)
    assert result["confirmed_complete"]
    uncertain_index = next(i for i, e in enumerate(events) if e["event"] == "execution_uncertain")
    assert events[uncertain_index + 1]["event"] == "observation"
    assert result["model_calls"] == 2


def test_evidence_kind_and_observation_budget_must_be_explicit():
    session = build_session()
    with pytest.raises(ValueError):
        Session(session.supervisor, session.observer, session.backend, "")
    for budget in (0, True, 1.2):
        with pytest.raises(ValueError):
            run_session(session, max_observations=budget)


def test_real_client_adapter_through_fake_transport_completes_loop():
    session = build_session()
    scripted_planner = session.supervisor.planner

    class FakeTransport:
        requests = []

        def chat(self, payload):
            self.requests.append(payload)
            request = json.loads(payload["messages"][1]["content"])
            output = scripted_planner.generate(request, ())
            return {"choices": [{"message": {"content": output}}],
                    "usage": {"total_tokens": 30}, "model": "test-only"}

    transport = FakeTransport()
    session.supervisor.planner = ClientPlanner(DashScopeVLMClient(transport=transport))
    result = run_session(session)
    assert result["confirmed_complete"]
    assert len(transport.requests) == 2
    assert len(result["model_usage"]) == 2
    assert sum(item["total_tokens"] for item in result["model_usage"]) == 60
    repair = json.loads(transport.requests[1]["messages"][1]["content"])
    assert repair["rejection_feedback"]["reason"] == "execution_feedback"
    assert len(repair["executed_history_not_current_state"]) == 1
