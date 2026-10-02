"""시험 Provider Policy -- 요금 한도 · 런타임 신뢰 범주로 공급자를 어떻게 할지(Sensor reference-provider-v1 을 DC 어휘로)."""
from . import Decision, default, get, pick

NAME = "dc-test-provider-2"     # -2: 모름 -> 기본 결정
RL, REL = "runtime.rate_limit_state", "runtime.runtime_reliability"


def decide(ctx) -> Decision:
    assert ctx.purpose == "provider_selection"
    rl, rel = get(ctx, RL), get(ctx, REL)
    used = (RL, REL)
    if rl in ("LIMITED", "EXHAUSTED") or rel == "FAILURE_OBSERVED":
        return Decision(NAME, ctx.id, pick(ctx, "SWITCH_PROVIDER", "WAIT"), f"요금 한도 {rl} · 신뢰 {rel}", used)
    unknown = tuple(k for k, v in ((RL, rl), (REL, rel)) if v is None)
    if unknown:                                          # BD-76: 하나라도 모르면 기본 결정(KEEP_PROVIDER)
        return default(ctx, NAME, unknown, used)
    return Decision(NAME, ctx.id, pick(ctx, "KEEP_PROVIDER"), f"요금 한도 {rl} · 신뢰 {rel}", used)
