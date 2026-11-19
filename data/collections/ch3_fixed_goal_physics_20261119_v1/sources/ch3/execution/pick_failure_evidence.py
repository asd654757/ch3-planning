"""Trace scope gates. No negative holding inference from a failed primitive."""


def failure_scope(result):
    if result.get('reason') != 'waypoint_timeout':
        return 'unknown'
    phases = [x['phase'] for x in result.get('trace', []) if x.get('steps', 0)>0]
    if not phases or max(phases)>3 or phases != list(range(max(phases)+1)):
        return 'unknown'
    return 'open_only' if max(phases)<=1 else 'closure_attempted'
