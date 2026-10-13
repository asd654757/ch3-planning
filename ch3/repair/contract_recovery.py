"""Model-generated contract recovery. No search, sorting or action synthesis."""
from copy import deepcopy
from .flexible_fixture_planner import evaluate_flexible, MOVABLE, REGIONS
from .persistent_model_recovery import strict_plan


def snapshot(state, goals, remaining_transfers, protected=()):
    return {'protocol':'contract_recovery_v1','evidence_kind':'symbolic_only',
        'current_observation':{'source':'controlled_symbolic_recovery_boundary',
            'facts':sorted(state.facts() | state.empty_hand_facts({'right'})),
            'occupancy':{'right':state.holding.get('right')},'uncertain_facts':[]},
        'goal_facts':[f'on({o}, {t})' for o,t in sorted(goals.items())],
        'remaining_goal_facts':[f'on({o}, {t})' for o,t in sorted(goals.items()) if state.location_of(o)!=t],
        'movable_objects':sorted(MOVABLE),'targets':sorted(REGIONS),'protected_objects':sorted(protected),
        'constraints':{'region_capacity':1,'remaining_transfers':remaining_transfers,
            'max_actions':min(8,2*remaining_transfers),
            'rules':['Adjacent pick/place pairs for same object, right arm only.',
                     'Placement region must contain no other cube at that candidate step.',
                     'Satisfy ALL goals and finish empty handed; do not move protected objects.']}}


def evaluate_output(text,state,goals,budget,protected=()):
    try:plan=strict_plan(text)
    except Exception as exc:return None,{'error_code':'PARSE_FAILURE','exception_type':type(exc).__name__}
    executable,error=evaluate_flexible(plan,state,goals,protected,
        execution_contract=True,remaining_transfers=budget)
    if error:
        error=deepcopy(error);error.pop('predicted_final_facts',None)
        error['candidate_executed']=False
        if 'transfer_index' in error:error['first_invalid_step']=2*error['transfer_index']+2
    return executable,error


def request_for(base,method,rejected=None,error=None):
    result=deepcopy(base)
    if method=='FULL':
        result['destination_occupancy']={t:sorted(o for o in MOVABLE
            if f'on({o}, {t})' in base['current_observation']['facts']) for t in REGIONS}
        result['task_obligations']=[{'fact':f,'remaining':f in base['remaining_goal_facts']} for f in base['goal_facts']]
    if rejected is not None:
        result['rejected_candidate_not_executed']=rejected
        result['feedback']=({'status':'rejected','regenerate_complete_suffix':True} if method=='DIRECT'
            else {'violation':error,'regenerate_complete_suffix':True})
    return result
