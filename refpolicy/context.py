"""시험 Context Policy -- 에이전트 맥락 압력(Sensor agent.context_pressure)으로 맥락을 어떻게 할지.

Sensor 판(reference-context-v1)의 COMPACT_CONTEXT(런타임 압축)는 DC 어휘(MS 맥락 동작)에 없다. 그래서 문턱 위는 COMPRESS,
창에 닿으면 DROP 으로 옮겼다 -- **같은 결정이 아니다**(런타임에 압축을 시키는 것과 우리가 덜 싣는 것은 다른 행동이다).
"""
from . import Decision, get, pick

NAME = "dc-test-context-1"
KEY = "agent.context_pressure"


def decide(ctx) -> Decision:
    assert ctx.purpose == "context_policy"
    p = get(ctx, KEY)
    if p is None:
        return Decision(NAME, ctx.id, pick(ctx, "KEEP"),
                        "맥락 압력을 모른다(또는 낡았다) -- 모르는 것으로 맥락을 줄이지 않는다", (KEY,))
    if p == "AT_CONTEXT_LIMIT":
        return Decision(NAME, ctx.id, pick(ctx, "DROP", "COMPRESS"), "창에 닿았다", (KEY,))
    if p == "ABOVE_COMPACTION_THRESHOLD":
        return Decision(NAME, ctx.id, pick(ctx, "COMPRESS", "DROP"), "런타임 압축 문턱을 넘었다", (KEY,))
    return Decision(NAME, ctx.id, pick(ctx, "KEEP"), f"맥락 압력 {p}", (KEY,))
