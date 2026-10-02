"""시험 실행 정책 -- 이어갈까 · 다시 할까 · 멈출까(Sensor reference-execution-v1 을 DC 어휘로).

Sensor 판에서는 문맥이 끝난 실행의 CONTINUE · RETRY · STOP 을 '가능하지 않음' 으로 걸렀다. DC 는 상태로 행동을 거르지 않으므로(I6)
그 판단을 여기서 한다: 끝난 실행에는 ESCALATE 또는 아무것도 하지 않음.
"""
from . import Decision, get, pick

NAME = "dc-test-execution-1"
COMP, HEALTH, INTR = "task.completion_state", "agent.execution_health", "agent.execution_interruption"
BUDGET, QUALITY = "agent.resource_state", "task.quality_state"
ENDED = ("ENDED_NORMALLY", "ENDED_BY_LIMIT", "ENDED_WITH_ERROR")


def decide(ctx) -> Decision:
    assert ctx.purpose == "execution_control"
    comp, health, intr = get(ctx, COMP), get(ctx, HEALTH), get(ctx, INTR)
    budget, q = get(ctx, BUDGET), get(ctx, QUALITY)
    if comp in ENDED:
        if q == "FAILED" or comp != "ENDED_NORMALLY":
            return Decision(NAME, ctx.id, pick(ctx, "ESCALATE"), f"끝남({comp}) · 외부 평가 {q}", (COMP, QUALITY))
        return Decision(NAME, ctx.id, None, f"끝남({comp}) -- 할 일 없음", (COMP,))
    if budget == "BUDGET_EXHAUSTED":
        return Decision(NAME, ctx.id, pick(ctx, "STOP"), "예산 소진", (BUDGET,))
    if health == "UNRESOLVED_FAILURES":
        why = "풀리지 않은 도구 실패" + (" (시간 초과)" if intr == "TIMEOUT_OBSERVED" else "")
        return Decision(NAME, ctx.id, pick(ctx, "RETRY", "ESCALATE"), why, (HEALTH, INTR))
    if comp is None:
        return Decision(NAME, ctx.id, pick(ctx, "ESCALATE"), "실행 상태를 모른다", (COMP,))
    return Decision(NAME, ctx.id, pick(ctx, "CONTINUE"), f"{comp} · 도구 {health}", (COMP, HEALTH))
