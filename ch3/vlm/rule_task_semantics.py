"""Transparent controlled-language baseline, no per-case IDs or gold access.

Consumes exactly the same known catalog and explicit held-blue fixture as VLM.
Not a universal language parser. Unrecognized clauses reject the WHOLE request.
"""
import re
from ch3.vlm.task_semantics import LocationGoal, TaskSemantics

BLUE, YELLOW = 'blue_candidate', 'yellow_candidate'
OBJECT = r'(?:the )?(?:blue(?: cube)?|yellow(?: cube)?|cube you (?:are holding|hold)|held cube)|(?:蓝色方块|蓝块|黄色方块|黄块|手里那个|手上的方块)'
DEST = r'(?:the )?(?:table|green(?: region)?)|(?:桌面|绿色区域)'


def refusal(status):
    return TaskSemantics(status=status,goals=[],forbidden_objects=[],placement_order=[],hand_empty=False)


def object_id(text):
    if re.search(r'yellow|黄', text): return YELLOW
    if re.search(r'blue|蓝|holding|hold|held|手', text): return BLUE
    raise ValueError('unrecognized object')


def target_id(text):
    return 'table' if re.search(r'table|桌', text) else 'green_region'


def parse_rule_semantics(instruction):
    text=instruction.strip().lower()
    # Shared catalog absent color references are not silently substituted.
    if re.search(r'\b(?:orange|purple|red|black|white)\s+(?:cube|block)\b|(?:橙|红|紫|黑|白)色?(?:方块|块)',text):
        return refusal('unsupported')
    if re.search(r'\b(?:push|press|throw|stack)\b|推动|按压|扔|堆叠',text):
        return refusal('unsupported')
    clauses=[c.strip(' ,，:：') for c in re.split(r'[.;。；\n]|\s+(?:and then|then|and)\s+|，',text) if c.strip(' ,，:：')]
    goals={};forbidden=set();order=[];empty=False
    for c in clauses:
        c=re.sub(r'^(?:now|instead)\s+','',c)
        if re.fullmatch(r'(?:cancel|forget) (?:the )?(?:old|previous|blue-to-green) (?:task|goal|instruction)|取消旧任务|取消之前的目标',c):
            continue
        if re.fullmatch(r'(?:finish|end)(?: with an empty hand| empty-handed)|最后空手|然后结束',c):
            empty=True;continue
        m=re.fullmatch(rf'(?:do not (?:touch|move|pick or place)|leave) (?P<obj>{OBJECT})(?: untouched)?|(?:不要碰|不要移动|禁止移动)(?P<cn>{OBJECT})',c)
        if m:
            forbidden.add(object_id(m.group('obj') or m.group('cn')));continue
        m=re.fullmatch(rf'(?:put|place|return|take|move|carry) (?P<obj>{OBJECT})(?: back)? (?:to|on|onto|in) (?P<dest>{DEST})|(?:把)?(?P<cn>{OBJECT})(?:放回|放到|送到|搬到)(?P<cndest>{DEST})',c)
        if m:
            obj=object_id(m.group('obj') or m.group('cn'));dest=target_id(m.group('dest') or m.group('cndest'))
            if obj in goals and goals[obj]!=dest: return refusal('clarify')
            goals[obj]=dest;empty=True;continue
        m=re.fullmatch(r'(?:put|place|move) both cubes (?:in|on|to) (?:the )?green(?: region)?|两个方块都放到绿色区域',c)
        if m:
            if any(g!='green_region' for g in goals.values()):return refusal('clarify')
            goals.update({BLUE:'green_region',YELLOW:'green_region'});empty=True;continue
        m=re.fullmatch(rf'(?P<first>{OBJECT}) first(?:,)? (?P<last>{OBJECT}) second|先放(?P<cnfirst>{OBJECT})再放(?P<cnlast>{OBJECT})',c)
        if m:
            if order:return refusal('clarify')
            order=[object_id(m.group('first') or m.group('cnfirst')),object_id(m.group('last') or m.group('cnlast'))]
            if len(set(order))!=2:return refusal('clarify')
            continue
        # Never extract only a recognizable fragment and drop unknown constraints.
        return refusal('clarify')
    if not goals or (order and not set(order)<=set(goals)):
        return refusal('clarify')
    result=TaskSemantics(status='ready',goals=[LocationGoal(object_id=o,target_id=t) for o,t in sorted(goals.items())],
        forbidden_objects=sorted(forbidden),placement_order=order,hand_empty=empty)
    result.check_scope()
    return result
