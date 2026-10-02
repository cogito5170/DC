"""시험 실행 정책 -- 이어갈까 · 다시 할까 · 멈출까(Sensor reference-execution-v1 을 DC 어휘로).

Sensor 판에서는 문맥이 끝난 실행의 CONTINUE · RETRY · STOP 을 '가능하지 않음' 으로 걸렀다. DC 는 상태로 행동을 거르지 않으므로(I6)
그 판단을 여기서 한다: 끝난 실행에는 ESCALATE 또는 아무것도 하지 않음.

BD-76: 규칙에 필요한 필수 상태(완료 · 실행 건강)를 모르면 목적의 기본 결정(ESCALATE, 사람이 없으면 STOP)을 쓴다.
아는 값만으로 정해지는 분기 셋(끝남 · 예산 소진 · 풀리지 않은 실패)은 그 앞에 남겼다.

BD-84 (CMD-D14): `NO_TOOL_RUN_YET`(실행 중인데 도구 호출이 아직 없다, Sensor execution-health-v3)은 **명시적 분기**로 CONTINUE 다 --
따질 도구 실패가 없고 실행은 돌고 있다. 결과를 볼 수 없는 UNKNOWN(SWE-agent)과는 다르다: 그것은 여전히 기본 결정이다.
CONTINUE 는 이 정책이 아는 값(RUNNING × NO_TOOL_RUN_YET · NO_FAILURE_OBSERVED · RECOVERED_FAILURES)에서만 나온다 -- 소스 어휘에 새
값이 생기면 맨 끝 분기에 우연히 떨어지지 않고 기본 결정으로 간다(이번에 NO_TOOL_RUN_YET 이 그렇게 CONTINUE 로 떨어졌었다).
"""
from . import Decision, default, get, pick

NAME = "dc-test-execution-3"     # -2: 필수 상태 모름 -> 기본 결정(BD-76) · -3: NO_TOOL_RUN_YET 분기, 아는 값에서만 CONTINUE(BD-84)
COMP, HEALTH, INTR = "task.completion_state", "agent.execution_health", "agent.execution_interruption"
BUDGET, QUALITY = "agent.resource_state", "task.quality_state"
ENDED = ("ENDED_NORMALLY", "ENDED_BY_LIMIT", "ENDED_WITH_ERROR")
HEALTHY = ("NO_FAILURE_OBSERVED", "RECOVERED_FAILURES")


def decide(ctx) -> Decision:
    assert ctx.purpose == "execution_control"
    comp, health, intr = get(ctx, COMP), get(ctx, HEALTH), get(ctx, INTR)
    budget, q = get(ctx, BUDGET), get(ctx, QUALITY)
    if comp in ENDED:
        if q == "FAILED" or comp != "ENDED_NORMALLY":
            return Decision(NAME, ctx.id, pick(ctx, "ESCALATE"), f"끝남({comp}) · 외부 평가 {q}", (COMP, QUALITY))
        return Decision(NAME, ctx.id, None, f"끝남({comp}) -- 할 일 없음", (COMP,))
    if budget == "BUDGET_EXHAUSTED":                     # 아는 값만으로 정해진다
        return Decision(NAME, ctx.id, pick(ctx, "STOP"), "예산 소진", (BUDGET,))
    if health == "UNRESOLVED_FAILURES":                  # 아는 값만으로 정해진다
        why = "풀리지 않은 도구 실패" + (" (시간 초과)" if intr == "TIMEOUT_OBSERVED" else "")
        return Decision(NAME, ctx.id, pick(ctx, "RETRY", "ESCALATE"), why, (HEALTH, INTR))
    unknown = tuple(k for k, v in ((COMP, comp), (HEALTH, health)) if v is None)
    if unknown:                                          # BD-76: 모름을 지나쳐 CONTINUE 로 가지 않는다
        return default(ctx, NAME, unknown, (COMP, HEALTH))
    if comp == "RUNNING" and health == "NO_TOOL_RUN_YET":   # BD-84: 아직 도구 호출이 없다 -- 따질 실패가 없다
        return Decision(NAME, ctx.id, pick(ctx, "CONTINUE"), "실행 중 · 도구 호출이 아직 없다(NO_TOOL_RUN_YET)",
                        (COMP, HEALTH))
    if comp == "RUNNING" and health in HEALTHY:
        return Decision(NAME, ctx.id, pick(ctx, "CONTINUE"), f"{comp} · 도구 {health}", (COMP, HEALTH))
    return default(ctx, NAME, tuple(f"{k}={v}(이 정책이 모르는 값)" for k, v in ((COMP, comp), (HEALTH, health))
                                    if not (v == "RUNNING" or v in HEALTHY + ("NO_TOOL_RUN_YET",))), (COMP, HEALTH))
