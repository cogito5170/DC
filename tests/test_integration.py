"""진짜 State 층에 붙여 본다 -- Sensor(llmsensor.state.StateEngine) · MS(usage_model). 옆 저장소가 없으면 건너뛴다.

    DC_SENSOR_PATH (기본 ../Sensor) · DC_MS_PATH (기본 ../MS)
"""
import importlib
import os
import sys
import unittest
from pathlib import Path

from dc import DecisionContextBuilder, MSUsageSource, SensorSource, policy_state

ROOT = Path(__file__).resolve().parents[1]
SENSOR = Path(os.environ.get("DC_SENSOR_PATH", ROOT.parent / "Sensor"))
MS = Path(os.environ.get("DC_MS_PATH", ROOT.parent / "MS"))


def _load(path: Path, mod: str):
    if not (path / mod.split(".")[0]).is_dir():
        return None
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        return importlib.import_module(mod)
    except Exception:
        return None


demo = _load(SENSOR, "eval.state_demo")
msmanager, msusage, mspolicy = (_load(MS, m) for m in ("ms.manager", "ms.usage_model", "ms.policy"))
msruntime = _load(MS, "ms.runtime")
# usage-model-3 부터 DENY 신호 이름이 arbiter_denies 다(WALP 를 뺐다)
DENY_SIG = ("interaction.arbiter_denies" if msusage and "arbiter_denies" in msusage.SESSION["properties"]
            else "interaction.walp_denies")


@unittest.skipIf(demo is None, f"Sensor 저장소가 없다: {SENSOR}")
class WithSensor(unittest.TestCase):
    def setUp(self):
        from llmsensor.state import StateEngine, from_telemetry
        self.E = StateEngine(demo.CFG).ingest_all(from_telemetry(demo.build() + [demo.end_record(None)]))
        self.src = SensorSource(self.E)
        self.b = DecisionContextBuilder([self.src])
        self.now = self.E.ledgers[demo.RUN].last_at
        self.subj = self.src.subject(demo.RUN)

    def ctx(self, now=None):
        return self.b.build("execution_control", self.subj, now_ms=self.now if now is None else now,
                            capabilities={"retry_budget": True})

    def test_values_match_sensor_and_nothing_is_out_of_domain(self):
        c = self.ctx()
        self.assertEqual(c.state("agent.execution_health").value, "UNRESOLVED_FAILURES")
        self.assertEqual(c.state("tool[WebFetch].tool_execution_health").value, "UNRESOLVED_FAILURES")
        self.assertEqual(c.state("task.completion_state").freshness, "PERMANENT")
        self.assertFalse([s.key for s in c.states for i in s.issues if i.code == "OUT_OF_DOMAIN"])
        # Sensor 안의 결정 문맥은 DC 로 합쳤다(PC-08). 이제 견줄 것은 Sensor 의 내보내기 계약 하나다
        for s in c.states:
            d = self.E.export_state(s.entity, s.name, self.now)
            self.assertEqual((s.value, s.source_status), (d["value"], d["status"]), s.key)
        self.assertFalse(hasattr(self.E, "decision_context"))     # Sensor 쪽 결정 문맥은 없다
        self.assertEqual(c.state("task.progress_state").status, "NOT_APPLICABLE")

    def test_reads_only_the_export_contract(self):
        """엔진 안(current · view · reg · cfg)을 감추고 계약 넷만 남겨도 같은 문맥이 나온다."""
        E = self.E

        class ContractOnly:
            EXPORT_CONTRACT = E.EXPORT_CONTRACT
            state_catalog, export_state, subjects, as_of = E.state_catalog, E.export_state, E.subjects, E.as_of
        b = DecisionContextBuilder([SensorSource(ContractOnly())])
        self.assertEqual(b.build("execution_control", self.subj, now_ms=self.now, capabilities={"retry_budget": True}
                                 ).digest, self.ctx().digest)

    def test_unknown_contract_version_is_refused(self):
        from dc import SourceError

        class Future:
            EXPORT_CONTRACT = "llmsensor.state-export/2"
        with self.assertRaises(SourceError):
            SensorSource(Future())

    def test_run_bound_now(self):
        src = SensorSource(self.E, run_id=demo.RUN)
        self.assertEqual(src.now_ms(), self.now)
        with self.assertRaises(ValueError):
            SensorSource(self.E).now_ms()

    def test_new_sensor_states_flow_through(self):
        c = self.ctx()
        for k in ("agent.execution_interruption", "task.quality_state"):
            self.assertIn(k, c.keys())
        self.assertFalse([s.key for s in c.states for i in s.issues if i.code == "OUT_OF_DOMAIN"])

    def test_evidence_refs_trace_back_to_observations(self):
        c = self.ctx()
        v = c.state("agent.execution_health")
        self.assertTrue(v.evidence_refs)
        self.assertTrue(set(v.evidence_refs) <= set(self.E.metrics))
        self.assertTrue(self.E.observation_ids(v.entity, v.name))

    def test_45_minutes_later_is_stale_but_completion_is_permanent(self):
        c = self.ctx(self.now + 45 * 60_000)
        self.assertEqual(c.state("agent.execution_health").status, "STALE")
        self.assertIsNone(c.value("agent.execution_health"))
        self.assertEqual(c.state("task.completion_state").status, "INFERRED")

    def test_llm_proposal_does_not_change_the_context(self):
        before = self.ctx()
        self.E.propose(f"agent:{demo.RUN}", "execution_health", "FAILING", "llm", "I think it is failing")
        self.assertEqual(self.ctx().digest, before.digest)

    def test_no_raw_measurement_values_leak(self):
        import json
        s = json.dumps(self.ctx().to_dict(), ensure_ascii=False)
        for raw in ("130000", "180000", "cache_read_input_tokens", "input_tokens"):
            self.assertNotIn(raw, s)


@unittest.skipIf(msmanager is None or msusage is None, f"MS 저장소가 없다: {MS}")
class WithMS(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.m = msmanager.StateManager(clock=lambda: self.clock[0])
        self.sid = msusage.open_session(self.m, "s1", {"token_budget": 20000, "context_budget": 4000,
                                                       "latency_budget_ms": 8000})
        sig = {"tokens.input_tokens": 19000, "tokens.context_tokens": 3000, "latency.total_ms": 2000,
               "interaction.llm_calls": 1, "interaction.retries": 0, "interaction.non_progress_rounds": 0,
               "interaction.proposal_invalid": 0, DENY_SIG: 0, "task.matched_rows": 5}
        for _ in range(3):           # usage-model-2 부터 품질 상태는 표본 3 개 이상에서만 판정한다
            for k, v in sig.items():
                self.m.ingest({"source": "ms:run", "entity": self.sid, "signal": k, "value": v, "ts": self.clock[0]})
            self.m.ingest({"source": "user", "entity": self.sid, "signal": "outcome.user_correction", "value": False,
                           "ts": self.clock[0]})
        self.src = MSUsageSource(self.m, msusage.MODEL_VERSION)
        self.b = DecisionContextBuilder([self.src])

    def build(self, purpose="context_policy"):
        return self.b.build(purpose, {"session": self.sid}, now_ms=self.src.now_ms())

    def test_policy_state_equals_ms_snapshot_when_fresh(self):
        ctx = self.build()
        self.assertTrue(ctx.validity.complete)
        st = policy_state(ctx, "session")
        snap = msusage.snapshot(self.m, self.sid)
        for k in ("token_budget_pressure", "context_pressure", "task_complexity", "answer_reliability",
                  "correction_rate"):
            self.assertEqual(st[k], snap[k], k)
        self.assertEqual(st["token_budget_pressure"], "HIGH")
        if mspolicy is not None:
            sel = mspolicy.AdaptiveContext()
            self.assertEqual(sel.plan(st, {})["params"], sel.plan(snap, {})["params"])

    def test_evidence_is_telemetry_ids_not_values(self):
        v = self.build().state("session.token_budget_pressure")
        self.assertTrue(v.evidence_refs)
        self.assertTrue(all(isinstance(r, str) and r.startswith("t") for r in v.evidence_refs))
        self.assertEqual(v.basis, "OPERATOR_ASSUMED")      # MS 의 문턱은 손으로 둔 것

    def test_stale_state_falls_back_to_fixed_policy_where_snapshot_does_not(self):
        if mspolicy is None:
            self.skipTest("ms.policy 없음")
        from dc.purpose import CONTEXT_POLICY
        strict = CONTEXT_POLICY.tightened("purpose-context-1-op120s",
                                          {"session.token_budget_pressure": 120_000, "session.context_pressure": 120_000})
        self.clock[0] += 300                                   # 5 분 동안 새 실행이 없다
        ctx = self.b.build(strict, {"session": self.sid}, now_ms=self.src.now_ms())
        st, snap = policy_state(ctx, "session"), msusage.snapshot(self.m, self.sid)
        self.assertIsNone(st["token_budget_pressure"])
        self.assertEqual(snap["token_budget_pressure"], "HIGH")   # MS 스냅숏은 낡았는지 보지 않는다
        sel = mspolicy.AdaptiveContext()
        self.assertEqual(sel.plan(st, {})["params"]["budget_chars"], mspolicy.BASE_CONTEXT["budget_chars"])
        self.assertLess(sel.plan(snap, {})["params"]["budget_chars"], mspolicy.BASE_CONTEXT["budget_chars"])

    def test_known_limit_config_input_ages_derived_state(self):
        """MS 파생의 시각 = 입력 중 가장 오래된 것. 예산(설정)도 입력이라, 실행이 방금 있어도 세션을 연 시각으로 늙는다.
        기본 목적은 max_age 를 안 줘서 영향이 없다 -- 엄한 목적을 MS 상태에 걸 때의 함정을 붙들어 둔다."""
        from dc.purpose import CONTEXT_POLICY
        self.clock[0] += 200
        self.m.ingest({"source": "ms:run", "entity": self.sid, "signal": "tokens.input_tokens", "value": 19500,
                       "ts": self.clock[0]})
        strict = CONTEXT_POLICY.tightened("p-op120s", {"session.token_budget_pressure": 120_000})
        v = self.b.build(strict, {"session": self.sid}, now_ms=self.src.now_ms()).state("session.token_budget_pressure")
        self.assertEqual(v.status, "STALE")
        self.assertAlmostEqual(v.age_ms, 200_000)


@unittest.skipIf(msruntime is None or "state_reader" not in getattr(getattr(msruntime, "Runtime", None), "__init__",
                                                                     lambda: 0).__code__.co_varnames,
                 "MS Runtime 에 state_reader 자리가 없다")
class WithMSRuntime(unittest.TestCase):
    """진짜 MS Runtime(모의 provider)에 MSStateReader 를 꽂는다 -- CR 이 결정 문맥을 거친 상태를 본다."""

    def _rt(self, purpose="context_runtime"):
        import json as _j
        from ms.providers import make_provider
        from ms.tools import ToolRegistry
        from dc import MSStateReader
        spec = _j.loads((MS / "ms/examples/datacenter.json").read_text(encoding="utf-8"))
        self.clock = [float(spec["now"])]
        m = msmanager.StateManager.from_spec(spec, clock=lambda: self.clock[0])
        for line in (MS / "ms/examples/datacenter_telemetry.jsonl").read_text(encoding="utf-8").splitlines():
            m.ingest(_j.loads(line))
        self.src = MSUsageSource(m, msusage.MODEL_VERSION)
        self.reader = MSStateReader(DecisionContextBuilder([self.src]), purpose)
        self.m = m
        rt = msruntime.Runtime(m, ToolRegistry(spec["tools"]), {"sim-claude": make_provider("sim-claude")},
                               context_selector=mspolicy.AdaptiveContext(), prompt_selector=mspolicy.AdaptivePrompt(),
                               base_context={"budget_chars": 1500}, state_reader=self.reader)
        rt.open_session("s", {"token_budget": 300, "context_budget": 200, "latency_budget_ms": 5000})
        return spec, rt

    def _go(self, rt, spec):
        out = rt.handle({"session": "s", "task": "srv07 을 throttle", "queries": spec["queries"]})
        self.assertEqual(out["record"]["decision_ref"], out["decision"]["id"])    # 결정 내용은 결정 기록에(baseline BD-15)
        return out["decision"]

    def test_runtime_records_the_context_it_used(self):
        from dc import from_dict
        spec, rt = self._rt()
        self._go(rt, spec)
        rec = self._go(rt, spec)
        src = rec["state_source"]
        self.assertEqual((src["kind"], src["id"], src["purpose"]), ("state_reader", self.reader.last.id,
                                                                    "context_runtime"))
        again = from_dict(self.reader.last.to_dict())                    # 기록에서 되살려도 digest 가 맞다
        self.assertEqual(again.digest, src["digest"])
        snap = msusage.snapshot(self.m, "session:s")
        for k in msusage.STATES:                                         # 신선하면 스냅숏과 같은 값
            if k in rec["state"] and k != "tool_churn":
                self.assertEqual(rec["state"][k], snap[k], k)
        self.assertEqual(rec["state"]["token_budget_pressure"], "HIGH")
        self.assertTrue(mspolicy.replay(rec)["ok"])

    def test_stale_pressure_reaches_cr_as_unknown(self):
        from dc.purpose import CONTEXT_RUNTIME
        strict = CONTEXT_RUNTIME.tightened("purpose-cr-1-op60s", {"session.token_budget_pressure": 60_000,
                                                                  "session.context_pressure": 60_000})
        spec, rt = self._rt(purpose=strict)
        self._go(rt, spec)
        self.clock[0] += 300                                             # 5 분 동안 새 실행이 없었다
        rec = self._go(rt, spec)
        self.assertEqual(msusage.snapshot(self.m, "session:s")["token_budget_pressure"], "HIGH")   # 스냅숏은 모른 척
        self.assertIsNone(rec["state"]["token_budget_pressure"])
        self.assertEqual(rec["context_policy"]["params"]["budget_chars"], 1500)          # 낡은 압력으로 줄이지 않았다
        self.assertIn("session.token_budget_pressure=STALE", rec["state_source"]["uncertain"])


@unittest.skipIf(demo is None or msmanager is None, "Sensor · MS 둘 다 있어야")
class BothSources(unittest.TestCase):
    def test_provider_selection_from_two_state_layers(self):
        from llmsensor.state import StateEngine, from_telemetry
        E = StateEngine(demo.CFG).ingest_all(from_telemetry(demo.build() + [demo.end_record(None)]))
        clock = [1000.0]
        m = msmanager.StateManager(clock=lambda: clock[0])
        sid = msusage.open_session(m, "s1", {"latency_budget_ms": 8000})
        for k, v in {"latency.total_ms": 9000, "interaction.llm_calls": 2, "interaction.proposal_invalid": 0,
                     DENY_SIG: 0}.items():
            m.ingest({"source": "ms:run", "entity": sid, "signal": k, "value": v, "ts": clock[0]})
        s, ms = SensorSource(E), MSUsageSource(m, msusage.MODEL_VERSION)
        ctx = DecisionContextBuilder([s, ms]).build(
            "provider_selection", {**s.subject(demo.RUN), "session": sid},
            now_ms={"sensor": E.ledgers[demo.RUN].last_at, "ms": ms.now_ms()},
            capabilities={"alternate_provider": True})
        self.assertEqual(ctx.state("session.latency_pressure").value, "HIGH")
        self.assertEqual(ctx.state("runtime.rate_limit_state").source, "sensor")
        self.assertIn("SWITCH_PROVIDER", ctx.available_actions)
        self.assertEqual(set(ctx.provenance.to_dict()["sources"]), {"sensor", "ms"})


if __name__ == "__main__":
    unittest.main()
