"""DC -- Decision Context. State 층(무엇이 일어나고 있나)과 Policy 층(무엇을 할까) 사이의 계약층.

    Telemetry  "무엇을 보았나"          (Sensor 의 telemetry · MS 의 RunRecord)
    State      "무엇이 일어나고 있나"    (Sensor 의 StateEngine -- 내보내기 계약으로 · MS 의 usage_model)
    DC         "이번 결정에 무엇이 중요한가"  ← 여기
    Policy     "무엇을 할까"             (MS 의 Context · Prompt · Provider Policy)

    model.py     형: DecisionContext = digest + Core(정책이 읽는 것) + Provenance(감사 · 재현). StateRecord · Constraint
    project.py   투영(저장 안 함): StateView · Validity · Action 목록
    purpose.py   목적별 투영 명세(context_runtime · context_policy · prompt_policy · provider_selection · execution_control)
    sources.py   State 층 어댑터(Sensor · MS · 표) -- 상태를 계산하지 않는다
    builder.py   Select -> Filter -> Validate -> Project -> Freeze
    snapshot.py  정준 JSON · 내용 해시 id · 기록에서 되살리기
    bridge.py    MS 정책 선택기가 받는 꼴로
    wiring.py    MS Runtime 의 state_reader 자리에 꽂는 리더
    store.py     고정된 문맥 보관(id 로 다시 꺼냄)
"""
from .model import (StateRecord, Constraint, Action, Issue, Validity, Core, CoreState, Provenance, StateProvenance,
                    DecisionContext, Subject, USABLE, STATUSES)
from .project import StateView
from .purpose import Purpose, StateRef, ActionSpec, PurposeError, PURPOSES
from .sources import StaticSource, SensorSource, MSUsageSource, SourceError
from .builder import DecisionContextBuilder, BUILDER_VERSION
from .snapshot import from_dict, SnapshotError, digest_of
from .bridge import policy_state, summary
from .wiring import MSStateReader
from .store import ContextStore

__all__ = ["StateRecord", "StateView", "Constraint", "Action", "Issue", "Validity", "Core", "CoreState", "Provenance",
           "StateProvenance", "DecisionContext",
           "Subject", "USABLE", "STATUSES", "Purpose", "StateRef", "ActionSpec", "PurposeError", "PURPOSES",
           "StaticSource", "SensorSource", "MSUsageSource", "SourceError", "DecisionContextBuilder", "BUILDER_VERSION",
           "from_dict", "SnapshotError", "digest_of", "policy_state", "summary", "MSStateReader", "ContextStore"]
