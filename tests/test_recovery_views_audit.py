from scripts.audit_recovery_views import VIEWS, view_evidence


def make_records():
    initial = {c: (100, 100) for c in VIEWS}
    records = [
        {c: {"blue": None, "green": None, "hand": (500, 500)} for c in VIEWS},
        {c: {"blue": None, "green": None, "hand": (0, 0)} for c in VIEWS},
    ]
    records[0]["corner"] = {"blue": (130, 140), "green": (50, 50), "hand": (130, 130)}
    records[1]["corner"] = {"blue": (140, 145), "green": (50, 50), "hand": (140, 135)}
    return initial, records


def test_no_permission_is_borrowed_from_other_camera_streams():
    initial, records = make_records()
    result = view_evidence(initial, records, "fresh:1")
    assert result["corner"]["continuation"]["action"] == "execute_remaining"
    assert result["corner2"]["continuation"]["action"] == "observe_again"
    assert result["topview"]["holding"]["status"] == "unknown"
    assert result["corner"]["continuation"]["observation_id"] == "fresh:1:corner"


def test_positive_holding_view_still_needs_its_own_fresh_destination():
    initial, records = make_records()
    records[-1]["corner"]["green"] = None
    result = view_evidence(initial, records, "fresh:2")
    assert result["corner"]["holding"]["status"] == "holding_supported"
    assert result["corner"]["continuation"]["action"] == "observe_again"


def test_stationary_visible_target_never_becomes_empty_hand_or_retry():
    initial, records = make_records()
    for i, record in enumerate(records):
        record["corner"].update(blue=(100, 100), hand=(100+10*i, 120))
    result = view_evidence(initial, records, "fresh:3")["corner"]
    assert result["nonfollowing"]["status"] == "target_not_following_supported"
    assert result["nonfollowing"]["retry_authorized"] is False
    assert result["continuation"]["action"] == "observe_again"
