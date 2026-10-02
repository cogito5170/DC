"""정책 쪽으로 넘기는 다리. MS 의 정책 선택기(`ms.policy`)는 {상태 이름: 값 또는 None} 을 받는다 -- None 은 "모름 = 고정대로".

`policy_state(ctx, role)` 은 그 꼴을 결정 문맥에서 만든다. 다른 점 하나가 요점이다:
    MS usage_model.snapshot()   그래프에 남은 파생 값을 그대로 낸다 -- 낡았는지 · 근거가 있는지 보지 않는다
    policy_state(ctx)           쓸 수 있는(usable) 상태만 값, STALE · INVALID · UNKNOWN 은 None

그래서 낡은 상태로 맥락을 줄이는 일이 생기지 않는다(정책은 None 을 고정 정책으로 읽는다). 정책 판본 · 규칙은 건드리지 않는다.
"""
from __future__ import annotations

from .model import DecisionContext


def policy_state(ctx: DecisionContext, role: str) -> dict:
    out = {}
    for c in ctx.core.states:
        head, name = c.key.rsplit(".", 1)
        if head.split("[", 1)[0] == role and "[" not in head:
            out[name] = ctx.value(c.key)          # core 만 읽는다 -- 쓸 수 있을 때만 값(선언된 STALE 도 여기선 None)
    out["decision_context"] = ctx.id
    return out


def summary(ctx: DecisionContext) -> str:
    """사람이 읽을 한 덩이(로그 · 시연용). LLM 에게 보일 글이 아니다."""
    L = [f"{ctx.id}  purpose={ctx.purpose}  as_of={dict(ctx.as_of)}  complete={ctx.validity.complete}"]
    for s in ctx.states:
        age = "-" if s.age_ms is None else f"{s.age_ms / 1000:.0f}s"
        mark = "" if s.usable else "  ✗"
        iss = (" [" + ", ".join(i.code for i in s.issues) + "]") if s.issues else ""
        L.append(f"  {s.key:<38} {str(s.value):<28} {s.status:<14} {s.freshness:<9} age={age:<6}{mark}{iss}")
    if ctx.validity.missing_required:
        L.append(f"  missing_required: {', '.join(ctx.validity.missing_required)}")
    if ctx.constraints:
        L.append("  constraints: " + ", ".join(f"{c.name} {c.op} {c.value}" for c in ctx.constraints))
    L.append("  actions: " + ", ".join(a.name if a.available else f"({a.name}: {'/'.join(a.missing)} 없음)"
                                       for a in ctx.actions))
    return "\n".join(L)
