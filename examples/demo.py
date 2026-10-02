"""시연 -- 진짜 State 층 둘(Sensor · MS)에서 목적별 결정 문맥을 짓는다.

    python3 examples/demo.py          (결과: examples/demo_output.txt)
    DC_SENSOR_PATH · DC_MS_PATH 로 옆 저장소 위치를 바꾼다(기본 ../Sensor · ../MS)

입력은 Sensor 의 §40 시연 레코드(실제 실행이 아니다)와, 손으로 넣은 MS 세션 신호 하나다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SENSOR = Path(os.environ.get("DC_SENSOR_PATH", ROOT.parent / "Sensor"))
MS = Path(os.environ.get("DC_MS_PATH", ROOT.parent / "MS"))
sys.path[1:1] = [str(SENSOR), str(MS)]

from dc import Constraint, DecisionContextBuilder, MSUsageSource, SensorSource, policy_state, summary  # noqa: E402
from dc.purpose import CONTEXT_POLICY  # noqa: E402


def main():
    from eval import state_demo as demo
    from llmsensor.state import StateEngine, from_telemetry
    from ms import usage_model as U
    from ms.manager import StateManager
    from ms.policy import AdaptiveContext

    L = []
    p = L.append
    E = StateEngine(demo.CFG).ingest_all(from_telemetry(demo.build() + [demo.end_record(None)]))
    clock = [1000.0]
    m = StateManager(clock=lambda: clock[0])
    sid = U.open_session(m, "s1", {"token_budget": 20000, "context_budget": 4000, "latency_budget_ms": 8000})
    deny = "interaction.arbiter_denies" if "arbiter_denies" in U.SESSION["properties"] else "interaction.walp_denies"
    for _ in range(3):        # usage-model-2 부터 품질 상태는 표본 3 개 이상에서만 판정한다
        for k, v in {"tokens.input_tokens": 19000, "tokens.context_tokens": 3000, "latency.total_ms": 9000,
                     "interaction.llm_calls": 1, "interaction.retries": 0, "interaction.non_progress_rounds": 0,
                     "interaction.proposal_invalid": 0, deny: 0, "task.matched_rows": 5}.items():
            m.ingest({"source": "ms:run", "entity": sid, "signal": k, "value": v, "ts": clock[0]})
        m.ingest({"source": "user", "entity": sid, "signal": "outcome.user_correction", "value": False, "ts": clock[0]})

    s, ms = SensorSource(E), MSUsageSource(m, U.MODEL_VERSION)
    B = DecisionContextBuilder([s, ms])
    subj = {**s.subject(demo.RUN), "session": sid}
    t_s = E.ledgers[demo.RUN].last_at

    def now(ds=0.0):
        return {"sensor": t_s + ds * 1000, "ms": ms.now_ms() + ds * 1000}

    caps = {"alternate_provider": True, "retry_budget": True, "retrieve_tool": True}

    def section(title):
        p("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100)

    section("1. 같은 State, 다른 목적 -> 다른 투영")
    for purpose, cons in (("context_policy", [Constraint("max_context_chars", "<=", 3000)]),
                          ("provider_selection", [Constraint("max_cost_usd", "<=", 0.10),
                                                  Constraint("max_latency_ms", "<=", 5000)]),
                          ("execution_control", [Constraint("max_retries", "<=", 2)])):
        p(summary(B.build(purpose, subj, now_ms=now(), constraints=cons, capabilities=caps)) + "\n")

    section("2. Freeze -- 내용 해시 id. 같은 입력이면 같은 id, State 가 바뀌어도 앞 문맥은 그대로")
    a = B.build("execution_control", subj, now_ms=now(), capabilities=caps)
    b = B.build("execution_control", subj, now_ms=now(), capabilities=caps)
    p(f"  두 번 지음: {a.id} == {b.id} -> {a.id == b.id}")
    E.propose(f"agent:{demo.RUN}", "execution_health", "FAILING", "llm", "I think it is failing")
    c = B.build("execution_control", subj, now_ms=now(), capabilities=caps)
    p(f"  LLM 제안(FAILING) 뒤: {c.id} -> 같은가 {c.id == a.id} (제안은 State 가 아니다)")
    p(f"  verify(): {a.verify()}")

    section("3. 45 분 뒤(새 관측 없음) -- STALE 은 지우지 않고 표시하며, 정책에는 '모름' 으로 간다")
    p(summary(B.build("execution_control", subj, now_ms=now(45 * 60), capabilities=caps)))

    section("4. 정책으로 -- MS AdaptiveContext 에 DC 를 거쳐 넘기기")
    sel = AdaptiveContext()
    snap = U.snapshot(m, sid)
    ctx = B.build("context_policy", subj, now_ms=now(), capabilities=caps)
    st = policy_state(ctx, "session")
    p(f"  지금:       snapshot 계획 budget={sel.plan(snap, {})['params']['budget_chars']}  "
      f"DC 계획 budget={sel.plan(st, {})['params']['budget_chars']}   (같다)")
    strict = CONTEXT_POLICY.tightened("purpose-context-1-op120s", {"session.token_budget_pressure": 120_000,
                                                                   "session.context_pressure": 120_000})
    clock[0] += 300
    ctx2 = B.build(strict, subj, now_ms=now(), capabilities=caps)
    st2, snap2 = policy_state(ctx2, "session"), U.snapshot(m, sid)
    p(f"  5 분 뒤, 운영자가 압력 상태에 max_age 120 s 를 건 목적(가정):")
    p(f"    snapshot: token_budget_pressure={snap2['token_budget_pressure']} -> budget "
      f"{sel.plan(snap2, {})['params']['budget_chars']}  ({'; '.join(sel.plan(snap2, {})['reasons'])})")
    p(f"    DC:       token_budget_pressure={st2['token_budget_pressure']} -> budget "
      f"{sel.plan(st2, {})['params']['budget_chars']}  ({'; '.join(sel.plan(st2, {})['reasons'])})")

    section("5. MS Runtime 에 꽂기 -- Runtime(..., state_reader=MSStateReader(builder)), 모의 provider")
    from ms.providers import make_provider
    from ms.runtime import Runtime
    from ms.tools import ToolRegistry
    from ms.policy import AdaptivePrompt
    from dc import MSStateReader
    from dc.purpose import CONTEXT_RUNTIME
    spec = json.loads((MS / "ms/examples/datacenter.json").read_text(encoding="utf-8"))
    for label, purpose, gap in (("context_runtime(기본)", "context_runtime", 0),
                                ("context_runtime + 압력 max_age 60 s(가정), 실행 사이 5 분", CONTEXT_RUNTIME.tightened(
                                    "purpose-cr-1-op60s", {"session.token_budget_pressure": 60_000,
                                                           "session.context_pressure": 60_000}), 300)):
        wclock = [float(spec["now"])]
        wm = StateManager.from_spec(spec, clock=lambda: wclock[0])
        for line in (MS / "ms/examples/datacenter_telemetry.jsonl").read_text(encoding="utf-8").splitlines():
            wm.ingest(json.loads(line))
        reader = MSStateReader(DecisionContextBuilder([MSUsageSource(wm, U.MODEL_VERSION)]), purpose)
        rt = Runtime(wm, ToolRegistry(spec["tools"]), {"sim-claude": make_provider("sim-claude")},
                     context_selector=AdaptiveContext(), prompt_selector=AdaptivePrompt(),
                     base_context={"budget_chars": 1500}, state_reader=reader)
        rt.open_session("s", {"token_budget": 300, "context_budget": 200, "latency_budget_ms": 5000})
        req = {"session": "s", "task": "srv07 을 throttle", "queries": spec["queries"]}
        rt.handle(req)
        wclock[0] += gap
        rec = rt.handle(req)["record"]["policy"]
        p(f"  [{label}]")
        p(f"    snapshot token_budget_pressure = {U.snapshot(wm, 'session:s')['token_budget_pressure']}")
        p(f"    CR 이 본 것                    = {rec['state']['token_budget_pressure']}  -> budget_chars "
          f"{rec['context_policy']['params']['budget_chars']}")
        p(f"    state_source = {json.dumps(rec['state_source'], ensure_ascii=False)}")

    section("6. 기록 꼴(to_dict) -- provider_selection, 줄임")
    d = B.build("provider_selection", subj, now_ms=now(), capabilities=caps,
                constraints=[Constraint("max_cost_usd", "<=", 0.10)]).to_dict()
    d["states"] = d["states"][:2]
    p(json.dumps(d, ensure_ascii=False, indent=1))

    out = "\n".join(L) + "\n"
    (ROOT / "examples" / "demo_output.txt").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
