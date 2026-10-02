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
    CAP = {"alternate_provider": True, "retry_budget": True, "human_reviewer": True}

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
        c = self.ctx([self.mc(0, 100, cr=150000, win=200000), self.end()], "context_policy")
        self.assertEqual(c.value("agent.context_pressure"), "ABOVE_COMPACTION_THRESHOLD")
        self.assertEqual(cpol.decide(c).action, "COMPRESS")
        d = cpol.decide(self.ctx([self.mc(0, 100)], "context_policy"))
        self.assertEqual(d.action, "KEEP")
        self.assertIn("모른다", d.reason)

    def test_stale_pressure_is_not_used_unless_policy_asks(self):
        late = 100 + 10 * MIN + 1
        c = self.ctx([self.mc(0, 100, cr=150000, win=200000), self.end()], "context_policy", now=late)
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
                for purpose, pol in (("context_policy", cpol), ("provider_selection", ppol),
                                     ("execution_control", epol)):
                    c = self.ctx(recs, purpose, caps)
                    d1, d2 = pol.decide(c), pol.decide(c)
                    self.assertEqual(d1, d2)
                    self.assertTrue(d1.action is None or d1.action in c.available_actions, (purpose, d1))
                    self.assertTrue(set(d1.used) <= set(c.keys()) | {r.key() for r in c_purpose(purpose).refs})


def c_purpose(name):
    from dc import PURPOSES
    return PURPOSES[name]


if __name__ == "__main__":
    unittest.main()
