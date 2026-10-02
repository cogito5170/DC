"""DC -- Decision Context. State 층(무엇이 일어나고 있나)과 Policy 층(무엇을 할까) 사이의 계약층.

    Telemetry  "무엇을 보았나"          (Sensor 의 telemetry · MS 의 RunRecord)
    State      "무엇이 일어나고 있나"    (Sensor 의 StateEngine · MS 의 usage_model)
    DC         "이번 결정에 무엇이 중요한가"  ← 여기
    Policy     "무엇을 할까"             (MS 의 Context · Prompt · Provider Policy)

    model.py     형: StateRecord · StateView · Constraint · Action · Validity · Provenance · DecisionContext
    purpose.py   목적별 투영 명세(context_policy · prompt_policy · provider_selection · execution_control)
    sources.py   State 층 어댑터(Sensor · MS · 표) -- 상태를 계산하지 않는다
    builder.py   Select -> Filter -> Validate -> Project -> Freeze
    snapshot.py  정준 JSON · 내용 해시 id · 기록에서 되살리기
    bridge.py    MS 정책 선택기가 받는 꼴로
"""
from .model import (StateRecord, StateView, Constraint, Action, Issue, Validity, Provenance, DecisionContext, Subject,
                    USABLE, STATUSES)
from .purpose import Purpose, StateRef, ActionSpec, PurposeError, PURPOSES
from .sources import StaticSource, SensorSource, MSUsageSource, SourceError
from .builder import DecisionContextBuilder, BUILDER_VERSION
from .snapshot import from_dict, SnapshotError, digest_of
from .bridge import policy_state, summary

__all__ = ["StateRecord", "StateView", "Constraint", "Action", "Issue", "Validity", "Provenance", "DecisionContext",
           "Subject", "USABLE", "STATUSES", "Purpose", "StateRef", "ActionSpec", "PurposeError", "PURPOSES",
           "StaticSource", "SensorSource", "MSUsageSource", "SourceError", "DecisionContextBuilder", "BUILDER_VERSION",
           "from_dict", "SnapshotError", "digest_of", "policy_state", "summary"]
