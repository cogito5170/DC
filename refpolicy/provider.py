"""시험 Provider Policy -- 요금 한도 · 런타임 신뢰 범주로 공급자를 어떻게 할지(Sensor reference-provider-v1 을 DC 어휘로)."""
from . import Decision, get, pick

NAME = "dc-test-provider-1"
RL, REL = "runtime.rate_limit_state", "runtime.runtime_reliability"


def decide(ctx) -> Decision:
    assert ctx.purpose == "provider_selection"
    rl, rel = get(ctx, RL), get(ctx, REL)
    used = (RL, REL)
    if rl in ("LIMITED", "EXHAUSTED") or rel == "FAILURE_OBSERVED":
        return Decision(NAME, ctx.id, pick(ctx, "SWITCH_PROVIDER", "WAIT"), f"요금 한도 {rl} · 신뢰 {rel}", used)
    if rl is None and rel is None:
        return Decision(NAME, ctx.id, pick(ctx, "KEEP_PROVIDER"),
                        "둘 다 모른다 -- 모르는 것으로 공급자를 바꾸지 않는다", used)
    return Decision(NAME, ctx.id, pick(ctx, "KEEP_PROVIDER"), f"요금 한도 {rl} · 신뢰 {rel}", used)
