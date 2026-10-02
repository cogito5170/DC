"""PC-08: Sensor 의 결정 문맥 · 참조 정책을 DC 로 합친 것.

    allow_stale · ContextStore  (Sensor decision/context 의 장점, BD-05)
    refpolicy                   (Sensor llmsensor/policy -> DC 기반 시험 정책, 진짜 Sensor 상태 위에서)
"""
import ast
import json
import unittest
from pathlib import Path

from dc import PURPOSES, ContextStore, DecisionContextBuilder, SensorSource, SnapshotError
from refpolicy import context as cpol, execution as epol, provider as ppol

from .helpers import MIN, NOW, builder, rec, sensor_source, subject
from .test_integration import demo  # 옆 Sensor 저장소를 sys.path 에 올리는 일을 같이 쓴다

ROOT = Path(__file__).resolve().parents[1]


class AllowStale(unittest.TestCase):
    def test_spec_and_policy_must_both_ask_for_stale(self):
        """CMD-D6: 목적 명세가 키마다 선언해야 STALE 값이 core 에 실리고, 정책도 allow_stale=True 로 불러야 받는다."""
        ctx = builder().build("execution_control", subject(), now_ms=NOW + 45 * MIN)
        self.assertEqual(ctx.state("agent.execution_health").status, "STALE")
        self.assertIsNone(ctx.value("agent.execution_health", allow_stale=True))       # 선언 없음 -> core 에 없다
        P = PURPOSES["execution_control"].tightened("purpose-execution-2-stale-ok",
                                                   allow_stale=("agent.execution_health",))
        ctx = builder().build(P, subject(), now_ms=NOW + 45 * MIN)
        self.assertIsNone(ctx.value("agent.execution_health"))                          # 정책이 명시 안 함
        self.assertEqual(ctx.value("agent.execution_health", allow_stale=True), "UNRESOLVED_FAILURES")
        self.assertIsNone(ctx.value("runtime.rate_limit_state", allow_stale=True))      # 선언 안 한 키는 그대로
        with self.assertRaises(Exception):
            PURPOSES["execution_control"].tightened("x", allow_stale=("no.such",))

    def test_allow_stale_does_not_unlock_unknown_or_invalid(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "FAILING"))            # 값 집합 밖 -> INVALID
        ctx = builder(sensor=s).build("execution_control", subject(), now_ms=NOW + 45 * MIN)
        self.assertIsNone(ctx.value("agent.execution_health", allow_stale=True))
        self.assertIsNone(ctx.value("task.progress_state", allow_stale=True))      # UNKNOWN


class Store(unittest.TestCase):
    def test_put_get_dump_load(self):
        a = builder().build("execution_control", subject(), now_ms=NOW)
        b = builder().build("provider_selection", subject(), now_ms=NOW)
        S = ContextStore()
        self.assertEqual(S.put(a), a.id)
        S.put(a)
        S.put(b)
        self.assertEqual((len(S), S.get(a.id)), (2, a))
        T = ContextStore().load(json.loads(json.dumps(S.dump())))
        self.assertEqual(T.get(b.id), b)

    def test_refuses_tampered(self):
        a = builder().build("execution_control", subject(), now_ms=NOW)
        object.__setattr__(a.core.states[0], "value", "NO_FAILURE_OBSERVED")
        with self.assertRaises(SnapshotError):
            ContextStore().put(a)
        rows = ContextStore().dump()
        good = builder().build("execution_control", subject(), now_ms=NOW).to_dict()
        good["core"]["states"]["agent.execution_health"][0] = "NO_FAILURE_OBSERVED"
        with self.assertRaises(SnapshotError):
            ContextStore().load(rows + [good])


class PoliciesReadOnlyTheContext(unittest.TestCase):
    def test_refpolicy_imports_nothing_but_itself(self):
        for p in (ROOT / "refpolicy").glob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + \
                   [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse([m for m in mods if m not in (None, "__future__", "dataclasses")], (p.name, mods))

    def test_dc_package_does_not_import_refpolicy(self):
        for p in (ROOT / "dc").glob("*.py"):
            self.assertNotIn("refpolicy", p.read_text(encoding="utf-8"), p.name)


@unittest.skipIf(demo is None, "Sensor 저장소가 없다")
class OnRealSensorStates(unittest.TestCase):
    """Sensor tests/test_decision_context.py 의 정책 시험을 DC 어휘로 옮긴 것."""
    RUN = "cc_stream:demo"
    CAP = {"alternate_provider": True, "retry_budget": True, "human_reviewer": True, "runtime_compaction": True}

    def mc(self, i, t, cr=1000, win=None):
        from llmsensor.telemetry.schema import record
        return record("model_call", self.RUN, "cc_stream", call_index=i, t_start_ms=t - 10, t_end_ms=t,
                      time_base="monotonic_ms", input_tokens=10, cache_read_input_tokens=cr,
                      cache_creation_input_tokens=100, output_tokens=50, thinking_tokens=20, stop_reason="tool_use",
                      tool_calls_per_message=1, output_text_chars=0, context_window=win, stream_thinking_estimate=None)

    def tc(self, i, j, t, err=False):
        from llmsensor.telemetry.schema import record
        return record("tool_call", self.RUN, "cc_stream", call_index=i, tool_index=j, tool_name="Bash",
                      tool_head="Bash:pytest", tool_sig="s", tool_input_chars=10, t_issued_ms=t - 5, t_result_ms=t,
                      time_base="monotonic_ms", is_error=err, tool_output_chars=3)

    def end(self, **kw):
        from llmsensor.telemetry.schema import record
        base = dict(result_subtype="success", terminal_reason="completed", is_error=False, cost_usd=0.05,
                    context_window=200000, autocompact_threshold=144000, rate_limit_utilization=0.5)
        base.update(kw)
        rn = [k for k, v in base.items() if v is None] + ([] if "api_error_status" in kw else ["api_error_status"])
        return record("run", self.RUN, "cc_stream", reported_null=rn, **{k: v for k, v in base.items() if v is not None})

    def ctx(self, recs, purpose, caps=None, now=None):
        from llmsensor.state import StateEngine, from_telemetry
        E = StateEngine().ingest_all(from_telemetry(recs))
        src = SensorSource(E, run_id=self.RUN)
        return DecisionContextBuilder([src]).build(purpose, src.subject(), now_ms=now if now is not None
                                                   else src.now_ms(), capabilities=self.CAP if caps is None else caps)

    def test_context_policy(self):
        """BD-58: 런타임 맥락은 agent_context -- Sensor 판과 같은 행동(COMPACT, 압축 못 하면 REDUCE)."""
        c = self.ctx([self.mc(0, 100, cr=150000, win=200000), self.end()], "agent_context")
        self.assertEqual(c.value("agent.context_pressure"), "ABOVE_COMPACTION_THRESHOLD")
        self.assertEqual(cpol.decide(c).action, "COMPACT")
        c = self.ctx([self.mc(0, 100, cr=150000, win=200000), self.end()], "agent_context", caps={})
        self.assertEqual(cpol.decide(c).action, "REDUCE")
        d = cpol.decide(self.ctx([self.mc(0, 100)], "agent_context"))
        self.assertEqual(d.action, "KEEP")
        self.assertIn("모른다", d.reason)

    def test_stale_pressure_is_not_used_unless_policy_asks(self):
        late = 100 + 10 * MIN + 1
        c = self.ctx([self.mc(0, 100, cr=150000, win=200000), self.end()], "agent_context", now=late)
        self.assertEqual(c.state("agent.context_pressure").status, "STALE")
        self.assertEqual(cpol.decide(c).action, "KEEP")
        self.assertIsNone(c.value("agent.context_pressure", allow_stale=True))         # 명세가 선언하지 않았다

    def test_provider_policy(self):
        c = self.ctx([self.mc(0, 100), self.end(api_error_status="429")], "provider_selection")
        self.assertEqual(ppol.decide(c).action, "SWITCH_PROVIDER")
        c = self.ctx([self.mc(0, 100), self.end(api_error_status="429")], "provider_selection", caps={})
        self.assertEqual(ppol.decide(c).action, "WAIT")
        self.assertEqual(ppol.decide(self.ctx([self.mc(0, 100), self.end()], "provider_selection")).action,
                         "KEEP_PROVIDER")

    def test_execution_policy(self):
        self.assertEqual(epol.decide(self.ctx([self.mc(0, 100), self.tc(0, 0, 110, err=True)],
                                              "execution_control")).action, "RETRY")
        self.assertEqual(epol.decide(self.ctx([self.mc(0, 100), self.tc(0, 0, 110)], "execution_control")).action,
                         "CONTINUE")
        ended = self.ctx([self.mc(0, 100), self.end()], "execution_control")
        self.assertIn("CONTINUE", ended.available_actions)        # DC 는 상태로 행동을 거르지 않는다(I6)
        self.assertIsNone(epol.decide(ended).action)              # 끝난 실행에서 하지 않을 것은 정책이 정한다

    def test_external_label_is_authority(self):
        """PC-14: quality_state 의 근거 EXTERNAL_LABEL 이 DC 어휘에 없어 거절되던 것(PC-08 이전에서 드러남)."""
        from llmsensor.sensing.quality import external_label_batch
        from llmsensor.state import StateEngine, from_telemetry
        E = StateEngine().ingest_all(from_telemetry([self.mc(0, 100), self.end()]))
        E.ingest(external_label_batch(self.RUN, False, "숨은 시험", "x"))
        src = SensorSource(E, run_id=self.RUN)
        c = DecisionContextBuilder([src]).build("execution_control", src.subject(), now_ms=src.now_ms(),
                                                capabilities=self.CAP)
        self.assertEqual((c.state("task.quality_state").basis, c.value("task.quality_state")), ("EXTERNAL_LABEL", "FAILED"))
        self.assertEqual(epol.decide(c).action, "ESCALATE")

    def test_only_available_actions_and_reproducible(self):
        cases = [[self.mc(0, 100)], [self.mc(0, 100, cr=150000, win=200000), self.tc(0, 0, 110, err=True)],
                 [self.mc(0, 100), self.end(api_error_status="429")], [self.mc(0, 100), self.end()]]
        for recs in cases:
            for caps in (self.CAP, {}):
                for purpose, pol in (("agent_context", cpol), ("provider_selection", ppol),
                                     ("execution_control", epol)):
                    c = self.ctx(recs, purpose, caps)
                    d1, d2 = pol.decide(c), pol.decide(c)
                    self.assertEqual(d1, d2)
                    self.assertTrue(d1.action is None or d1.action in c.available_actions, (purpose, d1))
                    self.assertTrue(set(d1.used) <= set(c.keys()) | {r.key() for r in c_purpose(purpose).refs})


def c_purpose(name):
    from dc import PURPOSES
    return PURPOSES[name]



class NoToolRunYet(unittest.TestCase):
    """BD-84 (CMD-D14): Sensor execution-health-v3 의 NO_TOOL_RUN_YET 을 시험 실행 정책이 명시적 분기로 다룬다."""

    def ctx(self, health, comp="RUNNING", status="INFERRED"):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", health, status, evidence=() if health is None else ("m1",)))
        if comp != "RUNNING":
            s.put(rec("sensor", "task:r1", "completion_state", None, "UNKNOWN", evidence=()))
        return builder(sensor=s).build("execution_control", subject(), now_ms=NOW,
                                       capabilities={"human_reviewer": True, "retry_budget": True})

    def test_running_with_no_tool_call_yet_continues_by_its_own_branch(self):
        c = self.ctx("NO_TOOL_RUN_YET")
        self.assertEqual(c.status("agent.execution_health"), "INFERRED")              # 쓸 수 있는 값이다(모름이 아니다)
        d = epol.decide(c)
        self.assertEqual((d.action, d.defaulted), ("CONTINUE", False))
        self.assertIn("NO_TOOL_RUN_YET", d.reason)
        self.assertEqual(d.used, ("task.completion_state", "agent.execution_health"))

    def test_unknown_completion_is_still_the_default(self):
        d = epol.decide(self.ctx("NO_TOOL_RUN_YET", comp="UNKNOWN"))
        self.assertEqual((d.action, d.defaulted), ("ESCALATE", True))

    def test_unobservable_results_stay_unknown_and_default(self):
        d = epol.decide(self.ctx(None, status="UNKNOWN"))                               # SWE-agent: 결과를 못 본다
        self.assertEqual((d.action, d.defaulted), ("ESCALATE", True))

    def test_a_value_the_policy_does_not_know_never_falls_into_continue(self):
        """소스 어휘에 새 값이 생겨도 맨 끝 분기에 우연히 떨어지지 않는다 -- 이번 NO_TOOL_RUN_YET 이 그랬다."""
        class Ctx:
            purpose, id, default_action, available_actions = "execution_control", "dc-x", "ESCALATE", ("CONTINUE", "ESCALATE")
            vals = {"task.completion_state": "RUNNING", "agent.execution_health": "SOMETHING_NEW"}
            def keys(self): return tuple(self.vals)
            def value(self, k, allow_stale=False): return self.vals.get(k)
        d = epol.decide(Ctx())
        self.assertEqual((d.action, d.defaulted), ("ESCALATE", True))
        self.assertIn("SOMETHING_NEW", d.reason)


if __name__ == "__main__":
    unittest.main()


class SafeDefault(unittest.TestCase):
    """BD-23 · BD-76 (CMD-D12): 필수 상태를 몰라 규칙을 못 정하면 목적의 default_decision -- 다른 분기로 지나가지 않는다."""

    def ctx(self, health="UNKNOWN", caps=None, purpose="execution_control"):
        s = sensor_source()
        if health == "UNKNOWN":
            s.put(rec("sensor", "agent:r1", "execution_health", None, "UNKNOWN", evidence=()))
        elif health == "STALE":
            s.put(rec("sensor", "agent:r1", "execution_health", "UNRESOLVED_FAILURES", at=NOW - 20 * MIN))
        caps = {"human_reviewer": True, "retry_budget": True} if caps is None else caps
        return builder(sensor=s).build(purpose, subject(), now_ms=NOW, capabilities=caps)

    def test_default_resolves_against_capabilities(self):
        self.assertEqual(self.ctx().default_action, "ESCALATE")
        self.assertEqual(self.ctx(caps={}).default_action, "STOP")            # 사람이 없으면 STOP (BD-23)
        self.assertEqual(self.ctx(purpose="provider_selection").default_action, "KEEP_PROVIDER")
        self.assertEqual(self.ctx(purpose="agent_context").default_action, "KEEP")
        self.assertEqual(self.ctx(purpose="context_runtime").default_action, "KEEP")
        self.assertEqual(self.ctx(purpose="prompt_policy").default_action, "FULL_INSTRUCTION")   # BD-81
        self.assertEqual(self.ctx().core_dict()["default_action"], "ESCALATE")   # MS 도 명세 없이 core 에서 읽는다

    def test_unknown_health_while_running_is_default_not_continue(self):
        c = self.ctx()
        self.assertEqual(c.value("task.completion_state"), "RUNNING")
        d = epol.decide(c)
        self.assertEqual((d.action, d.defaulted), ("ESCALATE", True))
        d = epol.decide(self.ctx(caps={"retry_budget": True}))
        self.assertEqual((d.action, d.defaulted), ("STOP", True))           # human_reviewer 없이 ESCALATE 하지 않는다

    def test_stale_failure_is_default_not_continue(self):
        """실데이터 cc_jsonl_self:self_sna i=154 와 같은 꼴: 미해결 실패가 TTL 을 넘겨 STALE -> RETRY 가 CONTINUE 로 가면 안 된다."""
        c = self.ctx(health="STALE")
        self.assertEqual(c.status("agent.execution_health"), "STALE")
        self.assertEqual(epol.decide(c).action, "ESCALATE")

    def test_known_value_branches_stay(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", None, "UNKNOWN", evidence=()))
        s.put(rec("sensor", "agent:r1", "resource_state", "BUDGET_EXHAUSTED"))
        c = builder(sensor=s).build("execution_control", subject(), now_ms=NOW, capabilities={"human_reviewer": True})
        d = epol.decide(c)
        self.assertEqual((d.action, d.defaulted), ("STOP", False))          # 예산 소진은 아는 값만으로 정해진다

    def test_other_policies_use_the_purpose_default(self):
        s = sensor_source()
        s.put(rec("sensor", "runtime:r1", "rate_limit_state", None, "UNKNOWN", evidence=()))
        c = builder(sensor=s).build("provider_selection", subject(), now_ms=NOW, capabilities={})
        self.assertEqual((ppol.decide(c).action, ppol.decide(c).defaulted), ("KEEP_PROVIDER", True))
        s.put(rec("sensor", "agent:r1", "context_pressure", None, "UNKNOWN", evidence=()))
        c = builder(sensor=s).build("agent_context", subject(), now_ms=NOW, capabilities={})
        self.assertEqual((cpol.decide(c).action, cpol.decide(c).defaulted), ("KEEP", True))

    def test_default_must_be_a_listed_action(self):
        from dc import Purpose, PurposeError
        from dc.purpose import ActionSpec
        with self.assertRaises(PurposeError):
            Purpose("p", "v1", (), actions=(ActionSpec("KEEP"),), default_decision=("PANIC",))
