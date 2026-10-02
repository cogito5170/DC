"""시험 Context Policy -- 에이전트 런타임 자신의 맥락(목적 agent_context, baseline BD-58)을 맥락 압력으로 어떻게 할지.

Sensor 판(reference-context-v1)과 같은 행동이다: 압축 문턱을 넘으면 COMPACT(런타임이 압축할 수 있으면), 창에 닿았거나 압축을
못 하면 REDUCE, 모르면 KEEP. (한때 context_policy 의 COMPRESS · DROP 으로 옮겼던 것을 BD-58 로 되돌렸다.)
"""
from . import Decision, default, get, pick

NAME = "dc-test-agent-context-2"     # -2: 모름 -> 기본 결정
KEY = "agent.context_pressure"


def decide(ctx) -> Decision:
    assert ctx.purpose == "agent_context"
    p = get(ctx, KEY)
    if p is None:                                        # BD-76: 목적의 기본 결정(agent_context = KEEP)
        return default(ctx, NAME, (KEY,), (KEY,))
    if p == "AT_CONTEXT_LIMIT":
        return Decision(NAME, ctx.id, pick(ctx, "REDUCE"), "창에 닿았다", (KEY,))
    if p == "ABOVE_COMPACTION_THRESHOLD":
        return Decision(NAME, ctx.id, pick(ctx, "COMPACT", "REDUCE"), "런타임 압축 문턱을 넘었다", (KEY,))
    return Decision(NAME, ctx.id, pick(ctx, "KEEP"), f"맥락 압력 {p}", (KEY,))
