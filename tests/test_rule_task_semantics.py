from ch3.vlm.rule_task_semantics import parse_rule_semantics


def test_multi_clause_not_old_single_template():
    c=parse_rule_semantics('Cancel the old task; return blue to table; move yellow to green; finish empty-handed.')
    assert c.status=='ready'
    assert {(g.object_id,g.target_id) for g in c.goals}=={('blue_candidate','table'),('yellow_candidate','green_region')}


def test_prohibition_is_not_dropped():
    c=parse_rule_semantics('Put the held cube on green; do not move yellow; finish empty-handed.')
    assert c.status=='ready' and c.forbidden_objects==['yellow_candidate']


def test_order_is_not_just_goal_set():
    c=parse_rule_semantics('Place both cubes in green; yellow first blue second; end empty-handed.')
    assert c.placement_order==['yellow_candidate','blue_candidate']


def test_missing_object_whole_request_refusal():
    c=parse_rule_semantics('Return blue to table; take orange cube to green.')
    assert c.status=='unsupported' and not c.goals


def test_unknown_clause_cannot_authorize_recognizable_part():
    c=parse_rule_semantics('Put blue on green; keep two centimeters away from yellow.')
    assert c.status=='clarify' and not c.goals


def test_cn_known_grammar():
    c=parse_rule_semantics('把手里那个放回桌面；不要移动黄块；最后空手。')
    assert c.status=='ready' and c.goals[0].target_id=='table' and c.forbidden_objects==['yellow_candidate']


def test_conflicting_destinations_clarify():
    assert parse_rule_semantics('Put blue on table; put blue on green.').status=='clarify'
