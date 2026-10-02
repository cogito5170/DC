"""CMD-D18 (baseline BD-123 B1): 에이전트 도구 호출 판정 목적 `agent_tool_call` -- 필수는 transcript 로 알 수 있는 것만.

SDK 훅(rlo-SDK)이 Claude Code 의 PreToolUse 에서 DC 문맥을 짓는다. `execution_control` 은 필수 둘(요금 한도 · 정체)이 transcript 로
서지 않아 정상일 때도 불완전하다 -- 이 목적은 실행 건강만 필수로 둔다.

진짜 길: 기록된 transcript(rlo-SDK `rlo/data/transcripts`) -> Telemetry cc_jsonl -> Sensor from_l0 -> DC -> (있으면) guard 의 문맥 투영.
옆 저장소: RLO_SDK_REPO (기본 ../rlo-SDK) · GUARD_REPO (기본 ../guard). 없으면 그 시험만 까닭과 함께 건너뛴다.
"""
import dataclasses
import os
import sys
import unittest
from pathlib import Path

from dc import PURPOSES, DecisionContextBuilder, SensorSource
from dc.purpose import AGENT_TOOL_CALL

from .helpers import NOW, builder, rec, sensor_source, subject
from .test_integration import ROOT, demo, why

RLO = Path(os.environ.get("RLO_SDK_REPO", ROOT.parent / "rlo-SDK"))
GUARD = Path(os.environ.get("GUARD_REPO", ROOT.parent / "guard"))


class Spec(unittest.TestCase):
    def test_registered_with_a_version_and_only_execution_health_required(self):
        P = PURPOSES["agent_tool_call"]
        self.assertIs(P, AGENT_TOOL_CALL)
        self.assertEqual(P.version, "purpose-agent-tool-call-1")
        self.assertEqual([f"{r.role}.{r.name}" for r in P.refs if r.required], ["agent.execution_health"])
        self.assertEqual({f"{r.role}.{r.name}" for r in P.refs if not r.required},
                         {"tool.tool_execution_health", "agent.execution_interruption", "task.completion_state",
                          "task.progress_state", "runtime.rate_limit_state", "agent.resource_state"})
        self.assertEqual([a.name for a in P.actions], ["PROCEED", "HOLD", "ESCALATE"])
        self.assertEqual(P.default_decision, ("HOLD",))

    def test_existing_purposes_untouched(self):
        self.assertEqual(PURPOSES["execution_control"].version, "purpose-execution-3")
        self.assertEqual([f"{r.role}.{r.name}" for r in PURPOSES["execution_control"].refs if r.required],
                         ["agent.execution_health", "task.progress_state", "task.completion_state",
                          "runtime.rate_limit_state"])


class Completeness(unittest.TestCase):
    """맞춘 원천에서: 요금 한도 · 정체를 몰라도 완전, 실행 건강을 모르면 불완전. execution_control 은 대조."""

    def ctx(self, purpose, health="NO_FAILURE_OBSERVED", rate_known=False, caps=None):
        s = sensor_source()
        s.put(rec("sensor", "agent:r1", "execution_health", health, "INFERRED" if health else "UNKNOWN",
                  evidence=("m1",) if health else ()))
        if not rate_known:
            s.put(rec("sensor", "runtime:r1", "rate_limit_state", None, "UNKNOWN", evidence=()))
        return builder(sensor=s).build(purpose, subject(), now_ms=NOW, capabilities=caps or {})

    def test_unknown_rate_limit_and_progress_do_not_make_it_incomplete(self):
        c = self.ctx("agent_tool_call")
        self.assertEqual((c.status("runtime.rate_limit_state"), c.status("task.progress_state")), ("UNKNOWN", "UNKNOWN"))
        self.assertTrue(c.validity.complete)
        self.assertFalse(self.ctx("execution_control").validity.complete)                     # 대조

    def test_unknown_execution_health_is_incomplete_and_default_is_hold(self):
        c = self.ctx("agent_tool_call", health=None)
        self.assertEqual(c.validity.missing_required, ("agent.execution_health",))
        self.assertEqual(c.default_action, "HOLD")
        self.assertNotIn("ESCALATE", c.available_actions)                                     # 사람 없음
        self.assertIn("ESCALATE", self.ctx("agent_tool_call", caps={"human_reviewer": True}).available_actions)


def _rlo_missing():
    if not (RLO / "rlo" / "data" / "transcripts").is_dir():
        return f"rlo-SDK 저장소가 없다: {RLO}"
    if demo is None:
        return why("Sensor")
    try:
        import telemetry.collect  # noqa: F401
    except Exception as e:
        return f"Telemetry import 실패: {type(e).__name__}: {e}"
    return None


@unittest.skipIf(_rlo_missing(), _rlo_missing() or "")
class OnRecordedTranscripts(unittest.TestCase):
    """rlo-SDK 의 기록된 Claude Code transcript 를 훅과 같은 길로 읽는다(rlo `TranscriptJudge.collect` · `view`)."""

    @classmethod
    def setUpClass(cls):
        if str(RLO) not in sys.path:
            sys.path.insert(0, str(RLO))
        from llmsensor.run_state import from_l0
        from telemetry.collect import from_cc_jsonl
        from rlo import hooks
        from rlo.example_hooks import hook_input, now_after
        cls.ctxs = {}
        for name in ("normal", "normal_no_current_use", "after_failure", "read_after_failure", "parallel", "first_call"):
            inp = hook_input(name)
            run = hooks.run_id_of(inp)
            rs = from_l0(from_cc_jsonl(inp["transcript_path"], run), clock=lambda n=name: now_after(n))
            src = SensorSource(rs.engine, run_id=run)
            for p in ("agent_tool_call", "execution_control"):
                cls.ctxs[(name, p)] = DecisionContextBuilder([src]).build(p, src.subject(), now_ms=now_after(name))

    def test_normal_and_after_failure_are_complete(self):
        for name in ("normal", "normal_no_current_use", "after_failure", "read_after_failure"):
            c = self.ctxs[(name, "agent_tool_call")]
            self.assertTrue(c.validity.complete, (name, c.validity.missing_required))
        self.assertEqual(self.ctxs[("normal", "agent_tool_call")].value("agent.execution_health"), "NO_FAILURE_OBSERVED")
        self.assertEqual(self.ctxs[("after_failure", "agent_tool_call")].value("agent.execution_health"),
                         "UNRESOLVED_FAILURES")

    def test_unknown_required_is_incomplete(self):
        for name in ("parallel", "first_call"):                  # 결과를 아직 못 본 호출 -- 실행 건강 UNKNOWN
            c = self.ctxs[(name, "agent_tool_call")]
            self.assertEqual(c.validity.missing_required, ("agent.execution_health",), name)

    def test_execution_control_is_incomplete_on_the_same_transcripts(self):
        for (name, p), c in self.ctxs.items():
            if p == "execution_control":
                self.assertFalse(c.validity.complete, name)
                self.assertIn("runtime.rate_limit_state", c.validity.missing_required)
                self.assertIn("task.progress_state", c.validity.missing_required)

    def test_guard_sees_the_same_completeness(self):
        if not (GUARD / "guard").is_dir():
            self.skipTest(f"guard 저장소가 없다: {GUARD}")
        if str(GUARD) not in sys.path:
            sys.path.insert(0, str(GUARD))
        try:
            from guard.dc_adapter import dcview_from_dc
        except Exception as e:
            self.skipTest(f"guard import 실패: {type(e).__name__}: {e}")
        for name in ("normal", "after_failure", "first_call"):
            c = self.ctxs[(name, "agent_tool_call")]
            v = dcview_from_dc(c.to_dict(), dataclasses.asdict(AGENT_TOOL_CALL))
            self.assertEqual(v.complete, name != "first_call", name)
            self.assertEqual(v.default_decision, ("HOLD",))


if __name__ == "__main__":
    unittest.main()
