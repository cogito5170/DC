"""DC 의 불변식. 앞 일곱(I1~I7)이 설계의 핵심이고, 뒤는 다섯 단계 각각의 행동이다.

    I1 원 텔레메트리가 결정 문맥에 들어가지 않는다
    I2 상태가 없으면 UNKNOWN 이지 추정값이 아니다
    I3 STALE 은 VALID(쓸 수 있음)로 바뀌지 않는다
    I4 결정 문맥은 만든 뒤 바뀌지 않는다
    I5 쓸 수 있는 상태는 모두 근거로 되짚을 수 있다
    I6 결정 문맥은 정책을 정하지 않는다(목적함수 · 고른 행동이 없다)
    I7 LLM 출력은 결정 문맥의 상태를 고치지 못한다
"""
import dataclasses
import json
import re
import unittest
from pathlib import Path

from dc import (PURPOSES, Constraint, DecisionContextBuilder, MSStateReader, PurposeError, SnapshotError, SourceError, StateRef,
                StaticSource, from_dict, policy_state)
from dc.purpose import CONTEXT_POLICY, Purpose, is_objective_word

from .helpers import MIN, NOW, builder, ms_source, rec, sensor_source, subject

CAPS = {"alternate_provider": True, "retry_budget": True}


def build(purpose="execution_control", b=None, now=NOW, **kw):
    return (b or builder()).build(purpose, subject(), now_ms=now, **kw)


class I1_NoRawTelemetry(unittest.TestCase):
    RAW = ("input_tokens", "output_tokens", "cache_read", "cache_creation", "observation", "tokens.", "cost_usd",
           "total_ms", "context_tokens")

    def test_serialized_context_has_no_raw_fields(self):
        for p in PURPOSES:
            d = build(p, capabilities=CAPS).to_dict()
            keys = set()

            def walk(x):
                if isinstance(x, dict):
                    for k, v in x.items():
                        keys.add(k)
                        walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)
            walk(d)
            for raw in self.RAW:
                self.assertFalse([k for k in keys if raw in k], f"{p}: 키에 {raw}")

    def test_non_scalar_value_is_refused(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", {"input_tokens": 18000, "cache_read": 130000}))
        v = build(b=builder(sensor=s)).state("agent.execution_health")
        self.assertEqual((v.value, v.status), (None, "INVALID"))
        self.assertIn("NOT_SCALAR", [i.code for i in v.issues])
        self.assertNotIn("18000", json.dumps(build(b=builder(sensor=s)).to_dict()))

    def test_record_type_has_no_slot_for_measurements(self):
        from dc import StateRecord
        names = {f.name for f in dataclasses.fields(StateRecord)}
        self.assertFalse(names & {"metrics", "observations", "telemetry", "raw", "tokens"})


class I2_UnknownIsNotEstimated(unittest.TestCase):
    def test_absent_state_is_unknown_with_no_value(self):
        ctx = build()
        v = ctx.state("task.progress_state")
        self.assertEqual((v.value, v.status), (None, "UNKNOWN"))
        self.assertIsNone(ctx.value("task.progress_state"))
        self.assertIn("task.progress_state=UNKNOWN", ctx.validity.uncertain)
        self.assertIn("task.progress_state", ctx.validity.missing_required)
        self.assertFalse(ctx.validity.complete)

    def test_missing_source_role_or_crash_stays_in_context_as_unknown(self):
        class Boom(StaticSource):
            def read(self, e, n, now):
                raise RuntimeError("down")
        b = DecisionContextBuilder([Boom("sensor")])            # ms 소스는 아예 없다
        ctx = b.build("provider_selection", {"runtime": "runtime:r1"}, now_ms=NOW)   # agent · session 역할 없음
        by = {s.key: s for s in ctx.states}
        self.assertEqual(set(by), {r.key() for r in PURPOSES["provider_selection"].refs})   # 하나도 안 빠졌다
        self.assertEqual({s.status for s in ctx.states}, {"UNKNOWN"})
        self.assertEqual({s.value for s in ctx.states}, {None})
        codes = {s.key: s.issues[0].code for s in ctx.states}
        self.assertEqual(codes["runtime.rate_limit_state"], "SOURCE_ERROR")
        self.assertEqual(codes["session.latency_pressure"], "UNBOUND_ROLE")

    def test_source_that_answers_a_different_question_is_not_trusted(self):
        class Liar(StaticSource):
            def read(self, e, n, now):
                return rec("sensor", e, "rate_limit_state", "AVAILABLE")
        ctx = DecisionContextBuilder([Liar("sensor"), ms_source()]).build("execution_control", subject(), now_ms=NOW)
        self.assertEqual(ctx.state("agent.execution_health").status, "UNKNOWN")
        self.assertEqual(ctx.state("agent.execution_health").issues[0].code, "SOURCE_ERROR")


class I3_StaleNeverValid(unittest.TestCase):
    def test_ttl_exceeded_is_stale_and_not_handed_to_policy(self):
        ctx = build(now=NOW + 45 * MIN)
        v = ctx.state("agent.execution_health")
        self.assertEqual((v.status, v.freshness), ("STALE", "STALE"))
        self.assertIsNone(v.value)                                # core 에는 값이 없다(PC-07)
        self.assertEqual(v.withheld, "UNRESOLVED_FAILURES")      # 설명용 값은 provenance 에
        self.assertIsNone(ctx.value("agent.execution_health"))
        self.assertIsNone(ctx.value("agent.execution_health", allow_stale=True))   # 명세가 선언 안 했다
        self.assertIn("STALE_TTL", [i.code for i in v.issues])

    def test_source_stale_is_not_revived_by_an_earlier_now(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED", "STALE"))
        v = build(b=builder(sensor=s)).state("agent.execution_health")     # DC 의 '지금' 으로는 1 분밖에 안 됐다
        self.assertEqual((v.status, v.freshness), ("STALE", "STALE"))
        self.assertFalse(v.usable)
        self.assertIn("STALE_AT_SOURCE", [i.code for i in v.issues])

    def test_purpose_can_be_stricter_than_source_ttl(self):
        strict = CONTEXT_POLICY.tightened("purpose-context-1-strict", {"session.token_budget_pressure": 30_000})
        ctx = builder().build(strict, subject(), now_ms=NOW)
        v = ctx.state("session.token_budget_pressure")
        self.assertEqual(v.status, "STALE")
        self.assertIn("STALE_PURPOSE", [i.code for i in v.issues])
        self.assertEqual(ctx.state("session.correction_rate").status, "INFERRED")   # 엄하게 안 한 것은 그대로

    def test_untimed_cannot_satisfy_a_freshness_requirement(self):
        s = sensor_source()
        s.put(rec("sensor", "runtime:r1", "rate_limit_state", "AVAILABLE", at=None))
        b = builder(sensor=s)
        self.assertTrue(b.build("provider_selection", subject(), now_ms=NOW).state("runtime.rate_limit_state").usable)
        strict = PURPOSES["provider_selection"].tightened("p-strict", {"runtime.rate_limit_state": MIN})
        v = b.build(strict, subject(), now_ms=NOW).state("runtime.rate_limit_state")
        self.assertEqual((v.status, v.issues[0].code), ("UNKNOWN", "UNTIMED_REQUIRED"))

    def test_permanent_fact_does_not_go_stale(self):
        s = sensor_source()
        s.put(rec("sensor", "task:r1", "completion_state", "ENDED_NORMALLY", permanent=True))
        v = build(b=builder(sensor=s), now=NOW + 1000 * MIN).state("task.completion_state")
        self.assertEqual((v.status, v.freshness), ("INFERRED", "PERMANENT"))

    def test_policy_state_nulls_everything_not_usable(self):
        ms = ms_source(values={"token_budget_pressure": "HIGH"})
        ms.put(rec("ms", "session:s1", "context_pressure", "HIGH", "STALE", ttl=None, evidence=("t1",)))
        st = policy_state(builder(ms=ms).build("context_policy", subject(), now_ms=NOW), "session")
        self.assertEqual(st["token_budget_pressure"], "HIGH")
        self.assertIsNone(st["context_pressure"])


class I4_Immutable(unittest.TestCase):
    def test_fields_cannot_be_assigned(self):
        ctx = build()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.purpose = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.states[0].value = "x"
        self.assertIsInstance(ctx.states, tuple)
        self.assertIsInstance(ctx.validity.usable, tuple)

    def test_later_state_change_does_not_reach_an_old_context(self):
        s = sensor_source()
        b = builder(sensor=s)
        old = b.build("execution_control", subject(), now_ms=NOW)
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED"))
        new = b.build("execution_control", subject(), now_ms=NOW)
        self.assertEqual(old.state("agent.execution_health").value, "UNRESOLVED_FAILURES")
        self.assertEqual(new.state("agent.execution_health").value, "NO_FAILURE_OBSERVED")
        self.assertNotEqual(old.id, new.id)
        self.assertTrue(old.verify())

    def test_tampering_is_detected(self):
        ctx = build()
        object.__setattr__(ctx.core.states[0], "value", "NO_FAILURE_OBSERVED")
        self.assertFalse(ctx.verify())
        ctx2 = build()
        object.__setattr__(ctx2.provenance.states[0], "evidence_refs", ())       # provenance 도 digest 가 덮는다
        self.assertFalse(ctx2.verify())

    def test_round_trip_and_tampered_record_is_refused(self):
        ctx = build(capabilities=CAPS, constraints=[Constraint("max_retries", "<=", 2)])
        d = json.loads(json.dumps(ctx.to_dict()))
        self.assertEqual(from_dict(d), ctx)
        d["core"]["states"]["agent.execution_health"][0] = "NO_FAILURE_OBSERVED"
        with self.assertRaises(SnapshotError):
            from_dict(d)


class I5_Traceable(unittest.TestCase):
    def test_every_usable_state_has_rule_and_evidence(self):
        for p in PURPOSES:
            for s in build(p).states:
                if s.usable:
                    self.assertTrue(s.rule_id and s.evidence_refs, f"{p}: {s.key}")

    def test_usable_without_evidence_is_rejected(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED", evidence=()))
        ctx = build(b=builder(sensor=s))
        v = ctx.state("agent.execution_health")
        self.assertEqual((v.status, v.source_status), ("INVALID", "INFERRED"))
        self.assertIn("agent.execution_health:NO_EVIDENCE", ctx.validity.rejected)

    def test_usable_without_rule_is_rejected(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED", rule=""))
        self.assertEqual(build(b=builder(sensor=s)).state("agent.execution_health").status, "INVALID")

    def test_provenance_names_sources_and_versions(self):
        ctx = build("provider_selection")
        prov = ctx.provenance.to_dict()
        self.assertEqual(ctx.core.purpose_version, "purpose-provider-4")
        self.assertEqual(prov["sources"], {"ms": {"model": "usage-model-1"}, "sensor": {"config": "test-v1"}})


class I6_NoPolicyInside(unittest.TestCase):
    def test_context_type_has_no_objective_or_choice(self):
        from dc import DecisionContext
        names = {f.name for f in dataclasses.fields(DecisionContext)}
        for bad in ("objective", "weights", "priority", "decision", "chosen", "action", "plan", "prompt", "messages"):
            self.assertNotIn(bad, names)

    def test_objective_cannot_be_smuggled_as_a_constraint(self):
        for name in ("minimize_tokens", "objective", "prefer_provider", "quality_weight", "maxReward"):
            self.assertTrue(is_objective_word(name), name)
            with self.assertRaises(PurposeError):
                build("provider_selection", constraints=[Constraint(name, "==", 1)])
        for name in ("max_cost_usd", "max_latency_ms", "allowed_providers"):
            self.assertFalse(is_objective_word(name), name)
        with self.assertRaises(PurposeError):
            Purpose("p", "v1", (), constraints=("minimize_cost",))
        spoofed = PURPOSES["provider_selection"].with_()           # 명세 검사를 우회해 목적함수 이름을 넣어도
        object.__setattr__(spoofed, "constraints", spoofed.constraints + ("minimize_cost",))
        with self.assertRaises(PurposeError):                       # 빌더가 한 번 더 막는다
            builder().build(spoofed, subject(), now_ms=NOW, constraints=[Constraint("minimize_cost", "==", 1)])

    def test_unknown_constraint_is_refused(self):
        with self.assertRaises(PurposeError):
            build("provider_selection", constraints=[Constraint("max_retries", "<=", 2)])    # 이 목적의 것이 아니다

    def test_actions_depend_on_capabilities_not_on_state(self):
        calm = build("provider_selection", capabilities=CAPS)
        s = sensor_source()
        s.put(rec("sensor", "runtime:r1", "rate_limit_state", "EXHAUSTED", ttl=5 * MIN))
        hot = build("provider_selection", b=builder(sensor=s), capabilities=CAPS)
        self.assertNotEqual(calm.digest, hot.digest)
        self.assertEqual(calm.actions, hot.actions)                # 상태가 달라도 행동 목록은 같다 -- 고르는 것은 정책
        none = build("provider_selection")
        sw = {a.name: a for a in none.actions}["SWITCH_PROVIDER"]
        self.assertEqual((sw.available, sw.missing), (False, ("alternate_provider",)))
        self.assertEqual(none.available_actions, ("KEEP_PROVIDER", "WAIT", "STOP"))

    def test_package_does_not_import_policy_prompt_or_providers(self):
        src = "\n".join(p.read_text(encoding="utf-8") for p in Path(__file__).resolve().parents[1].glob("dc/*.py"))
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)
        self.assertFalse([i for i in imports if i.split(".")[0] in ("ms", "llmsensor") or "prompt" in i
                          or "provider" in i], imports)


class I7_LLMCannotWrite(unittest.TestCase):
    def test_non_authoritative_source_cannot_register(self):
        class Proposals(StaticSource):
            authoritative = False
        with self.assertRaises(SourceError):
            DecisionContextBuilder([Proposals("llm")])

    def test_builder_has_no_write_path_into_a_context(self):
        from dc import DecisionContext
        public = [m for m in dir(DecisionContext) if not m.startswith("_")]
        self.assertFalse([m for m in public if m.startswith(("set", "update", "apply", "propose", "patch"))], public)

    def test_estimate_basis_is_not_authority(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED", basis="ESTIMATE"))
        v = build(b=builder(sensor=s)).state("agent.execution_health")
        self.assertEqual((v.status, v.issues[0].code), ("INVALID", "UNAUTHORIZED_BASIS"))


class Stages(unittest.TestCase):
    def test_deterministic_id(self):
        a, b = build(capabilities=CAPS), build(capabilities=CAPS)
        self.assertEqual(a.id, b.id)
        self.assertTrue(a.id.startswith("dc-") and len(a.id) == 19)
        self.assertNotEqual(a.id, build(now=NOW + 1, capabilities=CAPS).id)

    def test_projection_differs_by_purpose_and_holds_only_its_refs(self):
        keys = {p: set(build(p).keys()) for p in PURPOSES}
        self.assertNotEqual(keys["context_policy"], keys["provider_selection"])
        self.assertEqual(keys["provider_selection"], {r.key() for r in PURPOSES["provider_selection"].refs})
        self.assertNotIn("session.tool_churn", set().union(*keys.values()))      # 소스에 있어도 부르지 않으면 없다

    def test_same_state_name_from_two_sources_stays_apart(self):
        ctx = build("context_policy")
        self.assertEqual(ctx.state("session.context_pressure").value, "MEDIUM")                    # MS 의 띠
        self.assertEqual(ctx.state("agent.context_pressure").value, "BELOW_COMPACTION_THRESHOLD")   # Sensor 의 런타임 선언

    def test_tool_fanout(self):
        ctx = build()
        self.assertEqual(ctx.state("tool[WebFetch].tool_execution_health").value, "UNRESOLVED_FAILURES")
        self.assertEqual(ctx.state("tool[Bash].tool_execution_health").value, "RECOVERED_FAILURES")
        no_tools = dict(subject(), tool=())
        ctx2 = builder().build("execution_control", no_tools, now_ms=NOW)
        self.assertFalse([k for k in ctx2.keys() if k.startswith("tool")])

    def test_not_applicable_resolves_but_is_not_usable(self):
        ctx = build("provider_selection")
        v = ctx.state("agent.resource_state")
        self.assertFalse(v.usable)
        self.assertIn("agent.resource_state", ctx.validity.not_applicable)
        self.assertNotIn("agent.resource_state", ctx.validity.missing_required)

    def test_not_applicable_is_not_flagged_stale(self):
        v = build("provider_selection", now=NOW + 45 * MIN).state("agent.resource_state")
        self.assertEqual((v.status, v.issues), ("NOT_APPLICABLE", ()))

    def test_as_of_lists_only_registered_sources(self):
        b = DecisionContextBuilder([sensor_source()])
        ctx = b.build("provider_selection", subject(), now_ms=NOW)
        self.assertEqual(dict(ctx.as_of), {"sensor": NOW})
        self.assertEqual(ctx.state("session.latency_pressure").issues[0].code, "NO_SOURCE")

    def test_complete_when_all_required_resolved(self):
        self.assertTrue(build("provider_selection").validity.complete)
        self.assertTrue(build("context_policy").validity.complete)

    def test_out_of_domain_and_incoherent(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "FAILING"))       # LLM 이 쓸 법한 말 -- 값 집합 밖
        s.put(rec("sensor", "runtime:r1", "rate_limit_state", None))          # 쓸 수 있다는데 값이 없다
        ctx = build("execution_control", b=builder(sensor=s))
        self.assertEqual(ctx.state("agent.execution_health").issues[0].code, "OUT_OF_DOMAIN")
        self.assertEqual(ctx.state("runtime.rate_limit_state").issues[0].code, "INCOHERENT")

    def test_future_observation_is_invalid(self):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED", at=NOW + MIN))
        v = build(b=builder(sensor=s)).state("agent.execution_health")
        self.assertEqual((v.status, v.issues[0].code), ("INVALID", "FUTURE_OBSERVATION"))

    def test_per_source_now(self):
        b = builder()
        with self.assertRaises(ValueError):
            b.build("provider_selection", subject(), now_ms={"sensor": NOW})
        ctx = b.build("provider_selection", subject(), now_ms={"sensor": NOW, "ms": NOW + 5 * MIN})
        self.assertEqual(dict(ctx.as_of), {"sensor": NOW, "ms": NOW + 5 * MIN})
        self.assertAlmostEqual(ctx.state("session.latency_pressure").age_ms, 6 * MIN)
        with self.assertRaises(TypeError):
            b.build("provider_selection", subject(), now_ms=None)

    def test_constraints_are_sorted_and_validated(self):
        ctx = build("provider_selection", constraints=[
            Constraint("max_latency_ms", "<=", 5000), Constraint("allowed_providers", "in", ("claude", "openai")),
            {"name": "max_cost_usd", "op": "<=", "value": 0.10}])
        self.assertEqual([c.name for c in ctx.constraints], ["allowed_providers", "max_cost_usd", "max_latency_ms"])
        with self.assertRaises(PurposeError):
            build("provider_selection", constraints=[Constraint("max_cost_usd", "<", 1)])
        with self.assertRaises(PurposeError):
            build("provider_selection", constraints=[Constraint("allowed_providers", "in", "claude")])

    def test_purpose_spec_checks(self):
        with self.assertRaises(PurposeError):
            Purpose("p", "v1", (StateRef("a", "r", "x"), StateRef("a", "r", "x")))
        with self.assertRaises(PurposeError):
            Purpose("p", "v1", (StateRef("a", "r", "x"), StateRef("b", "r", "x")))      # 같은 키 -- 역할로 갈라라
        with self.assertRaises(PurposeError):
            CONTEXT_POLICY.tightened(CONTEXT_POLICY.version, {})
        with self.assertRaises(PurposeError):
            builder().build("nope", subject(), now_ms=NOW)


class Wiring(unittest.TestCase):
    """MS Runtime 의 state_reader 꼴(MS 없이): {"state": {상태: 값 | None}, "record": {...}}."""

    def test_reader_returns_ms_shape_and_records_the_context(self):
        seen = []
        r = MSStateReader(builder(), now_ms=NOW, sink=seen.append)
        out = r(None, "session:s1")
        names = {x.name for x in PURPOSES["context_runtime"].refs}
        self.assertEqual(set(out["state"]) - {"decision_context", "model_version"}, names)
        self.assertEqual(out["state"]["model_version"], "usage-model-1")
        self.assertEqual(out["record"]["id"], r.last.id)
        self.assertEqual(out["state"]["decision_context"], r.last.id)
        self.assertEqual(seen, [r.last])
        json.dumps(out)

    def test_reader_passes_unknown_as_none(self):
        ms = ms_source(values={"answer_reliability": None})
        out = MSStateReader(builder(ms=ms), now_ms=NOW)(None, "session:s1")
        self.assertIsNone(out["state"]["answer_reliability"])
        self.assertFalse(out["record"]["complete"])
        self.assertIn("session.answer_reliability=UNKNOWN", out["record"]["uncertain"])

    def test_reader_needs_a_now_it_can_trust(self):
        with self.assertRaises(ValueError):           # StaticSource 는 '지금' 을 모른다 -- 빌더도 리더도 시계를 지어내지 않는다
            MSStateReader(builder())(None, "session:s1")

    def test_context_actions_use_ms_vocabulary(self):
        ms_actions = ("KEEP", "SUMMARIZE", "RETRIEVE", "DROP", "DEFER", "COMPRESS")      # ms/context.py ACTIONS
        for p in ("context_policy", "context_runtime"):
            self.assertEqual(sorted(a.name for a in PURPOSES[p].actions), sorted(ms_actions))


if __name__ == "__main__":
    unittest.main()


class CoreProvenance(unittest.TestCase):
    """PC-07 (baseline BD-08 · SCHEMA §4): core = 정책이 읽는 것, provenance = 감사 · 재현, 나머지는 투영. reason 없음."""

    def test_no_reason_anywhere(self):
        from dc import StateRecord
        self.assertNotIn("reason", {f.name for f in dataclasses.fields(StateRecord)})
        for p in PURPOSES:
            self.assertNotIn('"reason"', json.dumps(build(p, capabilities=CAPS).to_dict(), ensure_ascii=False))

    def test_core_holds_only_what_policy_reads(self):
        c = build(capabilities=CAPS).core_dict()
        self.assertEqual(set(c), {"id", "purpose", "purpose_version", "as_of", "subject", "states", "constraints", "queries", "default_action",
                                  "actions"})
        for key, (value, status) in c["states"].items():
            if status not in ("OBSERVED", "DERIVED", "INFERRED"):
                self.assertIsNone(value, key)             # 쓸 수 없는 값은 core 에 없다
        blob = json.dumps(c, ensure_ascii=False)
        for prov_only in ("basis", "evidence_refs", "issues", "rule_id", "observed_at", "capabilities", "missing",
                          "withheld", "ttl_ms"):
            self.assertNotIn(prov_only, blob)

    def test_value_lives_exactly_once(self):
        ctx = build(now=NOW + 45 * MIN)
        for cs, pv in zip(ctx.core.states, ctx.provenance.states):
            self.assertEqual(cs.key, pv.key)
            self.assertFalse(cs.value is not None and pv.withheld is not None, cs.key)

    def test_reuse_key_ignores_as_of_but_not_state(self):
        a, b = build(capabilities=CAPS), build(now=NOW + 1, capabilities=CAPS)
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.reuse_key, b.reuse_key)
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", "NO_FAILURE_OBSERVED"))
        self.assertNotEqual(build(b=builder(sensor=s), capabilities=CAPS).reuse_key, a.reuse_key)
        self.assertNotEqual(build(now=NOW + 45 * MIN, capabilities=CAPS).reuse_key, a.reuse_key)   # STALE 이 되면 다르다

    def test_projection_needs_the_matching_spec(self):
        from dc import SnapshotError  # noqa: F401
        d = json.loads(json.dumps(build().to_dict()))
        self.assertTrue(from_dict(d).validity is not None)                # 등록된 이름@판본이면 찾는다
        strict = PURPOSES["execution_control"].tightened("purpose-execution-2-x", {"agent.execution_health": MIN})
        d2 = json.loads(json.dumps(builder().build(strict, subject(), now_ms=NOW).to_dict()))
        with self.assertRaises(LookupError):
            from_dict(d2).validity                                         # 판본이 다른 명세로는 투영하지 않는다
        self.assertTrue(from_dict(d2, purposes={strict.name: strict}).validity is not None)
        self.assertEqual(from_dict(d2).value("agent.execution_health"), "UNRESOLVED_FAILURES")    # core 읽기는 명세 없이도

    def test_core_is_a_small_part(self):
        from dc.snapshot import canonical
        for p in PURPOSES:
            ctx = build(p, capabilities=CAPS)
            core, full = len(canonical(ctx.core_dict()).encode()), len(canonical(ctx.to_dict()).encode())
            self.assertLess(core * 2, full, p)
