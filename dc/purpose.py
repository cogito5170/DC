"""목적(purpose) 명세 -- Decision Context 는 하나의 고정 꼴이 아니라 **목적별 투영**이다.

목적 하나가 정하는 것:
    refs          이번 결정에 필요한 상태(소스 · 역할 · 이름 · 필수 여부 · 엄한 max_age · 받는 근거 종류)
    constraints   받아들이는 제약 이름(모르는 이름은 거절 -- 제약 칸으로 목적함수를 몰래 넣지 못하게)
    actions       이 결정의 행동 목록과 각 행동에 필요한 **능력**(구조적 실행 가능성). 상태로 행동을 거르지 않는다
    version       명세를 바꾸면 올린다 -- 문맥의 provenance 에 남는다

목적은 정책이 아니다: "무엇을 알아야 하나" 만 말하고 "무엇을 할까" 는 말하지 않는다.

문턱을 지어내지 않는다: 기본 목적들은 max_age_ms 를 주지 않는다(소스 TTL 을 따른다). 더 엄한 신선도가 필요하면 운영자가
목적을 복제해 준다 -- 그 값은 잰 것이 아니라 가정이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .model import BASES, DEFAULT_ALLOWED_BASIS

# 제약 칸에 들어오면 안 되는 낱말 -- 목적함수 · 선호 · 가중치는 정책 소유다
OBJECTIVE_WORDS = ("objective", "minimize", "maximize", "minimise", "maximise", "prefer", "preference", "weight",
                   "priority", "goal", "reward", "utility", "score")


class PurposeError(ValueError):
    pass


@dataclass(frozen=True)
class StateRef:
    source: str                  # 등록된 소스 이름(sensor · ms ...)
    role: str                    # subject 의 역할(agent · task · runtime · tool · session ...)
    name: str                    # 소스 안의 상태 이름
    required: bool = True
    max_age_ms: "float | None" = None   # 소스 TTL 보다 엄한 신선도. None = 소스 TTL 을 따른다
    allowed_basis: tuple = DEFAULT_ALLOWED_BASIS
    values: "tuple | None" = None       # 값 집합을 목적이 못 박고 싶을 때. None = 소스가 아는 값 집합

    def key(self, tail: "str | None" = None) -> str:
        return f"{self.role}[{tail}].{self.name}" if tail else f"{self.role}.{self.name}"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    requires: tuple = ()
    meaning: str = ""


@dataclass(frozen=True)
class Purpose:
    name: str
    version: str
    refs: tuple
    constraints: tuple = ()      # 받아들이는 제약 이름
    actions: tuple = ()          # ActionSpec
    meaning: str = ""

    def __post_init__(self):
        seen = set()
        for r in self.refs:
            k = (r.source, r.role, r.name)
            if k in seen:
                raise PurposeError(f"{self.name}: 상태 {r.role}.{r.name}({r.source}) 가 두 번")
            seen.add(k)
            bad = [b for b in r.allowed_basis if b not in BASES]
            if bad:
                raise PurposeError(f"{self.name}: 모르는 근거 종류 {bad}")
            if r.max_age_ms is not None and r.max_age_ms <= 0:
                raise PurposeError(f"{self.name}: {r.name} 의 max_age_ms 는 양수")
        keys = [r.key() for r in self.refs]
        if len(set(keys)) != len(keys):
            raise PurposeError(f"{self.name}: 같은 역할 · 이름이 다른 소스에서 둘 -- 역할로 갈라라")
        for c in self.constraints:
            if is_objective_word(c):
                raise PurposeError(f"{self.name}: 제약 {c!r} 는 목적함수 낱말 -- 정책이 가진다")
        names = [a.name for a in self.actions]
        if len(set(names)) != len(names):
            raise PurposeError(f"{self.name}: 행동 이름이 겹친다")

    def with_(self, **kw) -> "Purpose":
        return replace(self, **kw)

    def tightened(self, version: str, max_age_ms: dict) -> "Purpose":
        """운영자 가정으로 신선도를 더 엄하게 한 복제본. 판본을 반드시 새로 준다."""
        if version == self.version:
            raise PurposeError("명세를 바꾸면 판본을 올린다")
        refs = tuple(replace(r, max_age_ms=max_age_ms.get(r.key(), r.max_age_ms)) for r in self.refs)
        return replace(self, version=version, refs=refs)


def is_objective_word(name: str) -> bool:
    words = re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower())
    return any(w in words for w in OBJECTIVE_WORDS)


# -- 기본 목적 넷 -------------------------------------------------------------------------------------------------
# 소스 이름: "sensor" = llmsensor.state.StateEngine(실행 단위 의미 상태), "ms" = MS usage_model(세션 단위 사용 상태)
S, M = "sensor", "ms"

CONTEXT_POLICY = Purpose(
    name="context_policy", version="purpose-context-1",
    meaning="LLM 에게 무엇을 보일지(Context Policy)를 고르기 위해 알아야 할 것",
    refs=(StateRef(M, "session", "token_budget_pressure"),
          StateRef(M, "session", "context_pressure"),
          StateRef(M, "session", "task_complexity"),
          StateRef(M, "session", "answer_reliability"),
          StateRef(M, "session", "correction_rate"),
          StateRef(S, "agent", "context_pressure", required=False),
          StateRef(S, "agent", "execution_health", required=False)),
    constraints=("max_context_chars", "must_keep"),
    actions=(ActionSpec("KEEP", meaning="맥락을 그대로"),
             ActionSpec("COMPRESS", meaning="같은 행을 더 짧게 그린다"),
             ActionSpec("SUMMARIZE", meaning="행 묶음을 요약으로"),
             ActionSpec("DROP", meaning="우선순위 낮은 행을 뺀다(must 제외)"),
             ActionSpec("DEFER", ("retrieve_tool",), "나중에 꺼내도록 미룬다 -- 꺼내는 도구가 있어야"),
             ActionSpec("COMPACT", ("runtime_compaction",), "런타임의 맥락 압축을 부른다")),
)

PROMPT_POLICY = Purpose(
    name="prompt_policy", version="purpose-prompt-1",
    meaning="LLM 에게 어떻게 말할지(Prompt Policy)를 고르기 위해 알아야 할 것",
    refs=(StateRef(M, "session", "token_budget_pressure"),
          StateRef(M, "session", "context_pressure"),
          StateRef(M, "session", "latency_pressure"),
          StateRef(M, "session", "task_complexity"),
          StateRef(M, "session", "answer_reliability"),
          StateRef(M, "session", "correction_rate"),
          StateRef(M, "session", "retry_pressure")),
    constraints=("max_output_tokens", "tool_permission"),
    actions=(ActionSpec("FULL_INSTRUCTION"), ActionSpec("CONCISE_INSTRUCTION"),
             ActionSpec("ADD_EXAMPLES"),
             ActionSpec("JSON_SCHEMA_OUTPUT", ("native_json_schema",), "provider 가 스키마를 강제할 수 있어야"),
             ActionSpec("SET_REASONING", ("reasoning_control",), "provider 가 추론 수준을 받아야"),
             ActionSpec("NARROW_TOOLS", meaning="도구를 좁힌다(넓히는 행동은 없다)")),
)

PROVIDER_SELECTION = Purpose(
    name="provider_selection", version="purpose-provider-1",
    meaning="누구에게 물을지(Provider Policy)를 고르기 위해 알아야 할 것",
    refs=(StateRef(S, "runtime", "rate_limit_state"),
          StateRef(S, "runtime", "runtime_reliability"),
          StateRef(M, "session", "latency_pressure"),
          StateRef(M, "session", "answer_reliability"),
          StateRef(S, "agent", "resource_state", required=False)),
    constraints=("max_cost_usd", "max_latency_ms", "allowed_providers", "data_residency"),
    actions=(ActionSpec("KEEP_PROVIDER"),
             ActionSpec("SWITCH_PROVIDER", ("alternate_provider",), "다른 provider 가 설정되어 있어야"),
             ActionSpec("RETRY", ("retry_budget",), "재시도 한도가 남아 있어야"),
             ActionSpec("STOP")),
)

EXECUTION_CONTROL = Purpose(
    name="execution_control", version="purpose-execution-1",
    meaning="실행을 이어 갈지 · 다시 할지 · 멈출지 정하기 위해 알아야 할 것",
    refs=(StateRef(S, "agent", "execution_health"),
          StateRef(S, "tool", "tool_execution_health", required=False),
          StateRef(S, "task", "progress_state"),
          StateRef(S, "task", "completion_state"),
          StateRef(S, "agent", "resource_state", required=False),
          StateRef(S, "runtime", "rate_limit_state")),
    constraints=("max_cost_usd", "max_retries", "require_tool_confirmation"),
    actions=(ActionSpec("CONTINUE"),
             ActionSpec("RETRY", ("retry_budget",)),
             ActionSpec("ESCALATE", ("human_reviewer",), "사람이 붙어 있어야"),
             ActionSpec("STOP")),
)

PURPOSES = {p.name: p for p in (CONTEXT_POLICY, PROMPT_POLICY, PROVIDER_SELECTION, EXECUTION_CONTROL)}
