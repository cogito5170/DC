"""DC 기반 시험 정책 -- **MS 정책이 아니다. `dc` 패키지의 일부도 아니다.** (baseline PC-08: Sensor `llmsensor/policy` 에서 옮겨 옴)

결정 문맥이 실제로 결정을 바꾸는지 재려고 지은 최소 결정론 정책이다. 입력은 `dc.DecisionContext` 하나뿐이다 --
State 저장소 · 텔레메트리 · 소스를 보지 않는다. 그래서 DC 꼴이 바뀌면 여기가 먼저 깨진다(그것이 이 정책의 쓸모다).

    context.py    agent_context       -> KEEP · REDUCE · COMPACT          (Sensor manage_context, BD-58)
    provider.py   provider_selection  -> KEEP_PROVIDER · SWITCH_PROVIDER · WAIT
    execution.py  execution_control   -> CONTINUE · RETRY · STOP · ESCALATE

규칙에 문턱은 없다 -- 상태의 범주 값만 본다. 모르는 상태(UNKNOWN · STALE · INVALID)를 어떻게 다룰지는 **정책이 명시적으로** 정한다.
DC 는 상태로 행동을 거르지 않으므로(불변식 I6), 끝난 실행에서 무엇을 하지 않을지도 정책이 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    policy: str
    context_id: str
    action: "str | None"
    reason: str
    used: tuple          # 결정에 쓴 문맥 키


def pick(ctx, *prefs):
    """선호 순서대로, 문맥이 가능하다고 한 첫 행동. 하나도 없으면 None."""
    return next((a for a in prefs if a in ctx.available_actions), None)


def get(ctx, key, allow_stale=False):
    """문맥에 그 키가 없으면(선택 상태가 펼쳐지지 않음) None."""
    return ctx.value(key, allow_stale) if key in ctx.keys() else None
