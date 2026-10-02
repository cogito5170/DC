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
    allow_stale: bool = False           # 이 키의 STALE 값을 core 에 싣는다(정책이 낡은 값을 쓰겠다고 명세가 선언, CMD-D6)

    def key(self, tail: "str | None" = None) -> str:
        return f"{self.role}[{tail}].{self.name}" if tail else f"{self.role}.{self.name}"


# 질의 명세의 칸 -- MS StateQuery 와 같은 낱말(ms/query.py). DC 는 질의를 **돌리지 않는다**: 소스가 돌리고 DC 는 결과를 검사 · 고정한다
QUERY_KEYS = ("name", "model", "where", "related", "ids", "select", "order_by", "limit", "priority", "must", "droppable")


@dataclass(frozen=True)
class QueryRef:
    """질의형 선택(baseline BD-26 · PC-23) -- 결정에 필요한 실체 행들을 소스에게 묻는다."""
    source: str
    spec: str                    # 질의 명세의 정준 JSON(QUERY_KEYS 만). 이름은 spec["name"]
    allow_stale: bool = False    # 낡은 속성 값도 core 에 싣는다(명세가 선언해야만)

    @property
    def name(self) -> str:
        import json
        return json.loads(self.spec)["name"]

    @classmethod
    def of(cls, source: str, spec: dict, allow_stale: bool = False) -> "QueryRef":
        import json
        bad = set(spec) - set(QUERY_KEYS)
        if bad or "name" not in spec:
            raise PurposeError(f"질의 명세: 모르는 칸 {sorted(bad)} 또는 이름 없음 -- 받는 칸 {QUERY_KEYS}")
        return cls(source, json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":")), allow_stale)


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
    queries: tuple = ()          # QueryRef -- 목적에 고정한 질의
    query_sources: tuple = ()    # 요청마다 질의를 받아도 되는 소스(CR 처럼 요청이 질의를 정할 때)
    default_decision: tuple = ()  # 안전 기본 결정(baseline BD-23 · BD-76): 순서 있는 후보, 가능한 첫 행동. 규칙이 필수 상태를
                                  # 몰라 정해지지 않을 때 정책이 쓴다. 비면 기본 결정이 없다

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
        bad_default = [a for a in self.default_decision if a not in names]
        if bad_default:
            raise PurposeError(f"{self.name}: 기본 결정 {bad_default} 이 행동 목록에 없다")
        qn = [q.name for q in self.queries]
        if len(set(qn)) != len(qn):
            raise PurposeError(f"{self.name}: 질의 이름이 겹친다")

    def with_(self, **kw) -> "Purpose":
        return replace(self, **kw)

    def tightened(self, version: str, max_age_ms: "dict | None" = None, allow_stale: tuple = ()) -> "Purpose":
        """운영자 가정으로 신선도를 더 엄하게 하거나 키별로 낡은 값을 허락한 복제본. 판본을 반드시 새로 준다."""
        if version == self.version:
            raise PurposeError("명세를 바꾸면 판본을 올린다")
        max_age_ms = max_age_ms or {}
        unknown = set(allow_stale) - {r.key() for r in self.refs}
        if unknown:
            raise PurposeError(f"{self.name}: 없는 키에 allow_stale {sorted(unknown)}")
        refs = tuple(replace(r, max_age_ms=max_age_ms.get(r.key(), r.max_age_ms),
                             allow_stale=r.allow_stale or r.key() in allow_stale) for r in self.refs)
        return replace(self, version=version, refs=refs)


def is_objective_word(name: str) -> bool:
    words = re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower())
    return any(w in words for w in OBJECTIVE_WORDS)


# -- 기본 목적 여섯 -------------------------------------------------------------------------------------------------
# 소스 이름: "sensor" = llmsensor.state.StateEngine(실행 단위 의미 상태), "ms" = MS usage_model(세션 단위 사용 상태)
S, M = "sensor", "ms"

# 맥락 동작은 MS 의 어휘 그대로다(ms/context.py ACTIONS). 꺼내는 길(RETRIEVE · DEFER)은 retrieve 도구가 있어야 한다.
MS_CONTEXT_ACTIONS = (ActionSpec("KEEP", meaning="행을 그대로 싣는다"),
                      ActionSpec("COMPRESS", meaning="같은 행을 표 꼴로 짧게"),
                      ActionSpec("SUMMARIZE", meaning="못 실은 행 묶음을 결정론적 집계로"),
                      ActionSpec("RETRIEVE", ("retrieve_tool",), "못 실은 행을 손잡이로만 -- 꺼내는 도구가 있어야"),
                      ActionSpec("DROP", meaning="droppable 행을 뺀다(must 제외)"),
                      ActionSpec("DEFER", ("retrieve_tool",), "질의 하나를 통째로 미룬다 -- 꺼내는 도구가 있어야"))

CONTEXT_POLICY = Purpose(
    name="context_policy", version="purpose-context-3",     # -3: default_decision KEEP (BD-23 · BD-76)
    meaning="LLM 에게 무엇을 보일지(MS CR 의 Context Policy)를 고르기 위해 알아야 할 것",
    refs=(StateRef(M, "session", "token_budget_pressure"),
          StateRef(M, "session", "context_pressure"),
          StateRef(M, "session", "task_complexity"),
          StateRef(M, "session", "answer_reliability"),
          StateRef(M, "session", "correction_rate"),
          StateRef(S, "agent", "context_pressure", required=False),
          StateRef(S, "agent", "execution_health", required=False)),
    constraints=("max_context_chars", "must_keep"),
    actions=MS_CONTEXT_ACTIONS,
    default_decision=("KEEP",),
)

PROMPT_POLICY = Purpose(
    name="prompt_policy", version="purpose-prompt-2",     # -2: default_decision FULL_INSTRUCTION (BD-81)
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
    default_decision=("FULL_INSTRUCTION",),     # BD-23 의 '고정 프롬프트 계획' = MS FIXED_PROMPT(instruction_mode: full) (BD-81)
)

PROVIDER_SELECTION = Purpose(
    name="provider_selection", version="purpose-provider-4",     # -4: default_decision KEEP_PROVIDER
    meaning="누구에게 물을지(Provider Policy)를 고르기 위해 알아야 할 것",
    refs=(StateRef(S, "runtime", "rate_limit_state"),
          StateRef(S, "runtime", "runtime_reliability"),
          StateRef(M, "session", "latency_pressure"),
          StateRef(M, "session", "answer_reliability"),
          StateRef(S, "agent", "resource_state", required=False),
          StateRef(S, "agent", "latency_state", required=False)),
    constraints=("max_cost_usd", "max_latency_ms", "allowed_providers", "data_residency"),
    actions=(ActionSpec("KEEP_PROVIDER"),
             ActionSpec("SWITCH_PROVIDER", ("alternate_provider",), "다른 provider 가 설정되어 있어야"),
             ActionSpec("RETRY", ("retry_budget",), "재시도 한도가 남아 있어야"),
             ActionSpec("WAIT", meaning="요금 한도 등이 풀릴 때까지 기다린다(Sensor 의 행동, BD-30)"),
             ActionSpec("STOP")),
    default_decision=("KEEP_PROVIDER",),
)

EXECUTION_CONTROL = Purpose(
    name="execution_control", version="purpose-execution-3",     # -3: default_decision ESCALATE, 못 하면 STOP
    meaning="실행을 이어 갈지 · 다시 할지 · 멈출지 정하기 위해 알아야 할 것",
    refs=(StateRef(S, "agent", "execution_health"),
          StateRef(S, "tool", "tool_execution_health", required=False),
          StateRef(S, "task", "progress_state"),
          StateRef(S, "task", "completion_state"),
          StateRef(S, "agent", "resource_state", required=False),
          StateRef(S, "runtime", "rate_limit_state"),
          StateRef(S, "agent", "execution_interruption", required=False),
          StateRef(S, "task", "quality_state", required=False)),
    constraints=("max_cost_usd", "max_retries", "require_tool_confirmation"),
    actions=(ActionSpec("CONTINUE"),
             ActionSpec("RETRY", ("retry_budget",)),
             ActionSpec("ESCALATE", ("human_reviewer",), "사람이 붙어 있어야"),
             ActionSpec("STOP")),
    default_decision=("ESCALATE", "STOP"),      # 사람이 없으면 남는 보수적 행동이 STOP (BD-23)
)

# 에이전트 런타임 자신의 맥락(예: Claude Code 자동 압축) -- MS 가 LLM 에 무엇을 보일지(context_policy)와 다른 결정이다(baseline BD-58 · PC-15).
AGENT_CONTEXT = Purpose(
    name="agent_context", version="purpose-agent-context-2",     # -2: default_decision KEEP
    meaning="에이전트 런타임이 자기 맥락을 어떻게 다룰지(압축을 시킬지 · 덜어 낼지 · 그대로 둘지) 정하기 위해 알아야 할 것",
    refs=(StateRef(S, "agent", "context_pressure"),
          StateRef(S, "agent", "execution_interruption", required=False)),
    constraints=("max_context_tokens",),
    actions=(ActionSpec("KEEP", meaning="런타임 맥락을 그대로"),
             ActionSpec("REDUCE", meaning="런타임 맥락을 덜어 낸다(요약 없이 -- 새 대화 등)"),
             ActionSpec("COMPACT", ("runtime_compaction",), "런타임에 맥락 압축을 시킨다 -- 런타임이 압축을 할 수 있어야")),
    default_decision=("KEEP",),
)

# MS CR(ms/cr.py) 의 plan(state) 한 번이 보는 것 전부: AdaptiveContext + AdaptivePrompt 가 읽는 세션 상태의 합집합.
# MS Runtime 의 state_reader 자리에 꽂을 때 이 목적을 쓴다(dc/wiring.py). 프롬프트 쪽은 행동이 아니라 계획 칸이라 행동에 넣지 않았다.
CONTEXT_RUNTIME = Purpose(
    name="context_runtime", version="purpose-cr-3",     # -2: 요청 질의(PC-23) · -3: default_decision KEEP
    meaning="MS Context Runtime 이 이번 요청의 맥락 · 프롬프트 계획을 정하기 위해 알아야 할 것",
    refs=(StateRef(M, "session", "token_budget_pressure"),
          StateRef(M, "session", "context_pressure"),
          StateRef(M, "session", "latency_pressure"),
          StateRef(M, "session", "task_complexity"),
          StateRef(M, "session", "answer_reliability"),
          StateRef(M, "session", "correction_rate"),
          StateRef(M, "session", "retry_pressure")),
    constraints=("max_context_chars", "max_output_tokens", "tool_permission"),
    actions=MS_CONTEXT_ACTIONS,
    query_sources=("ms_world",),
    default_decision=("KEEP",),
)

PURPOSES = {p.name: p for p in (CONTEXT_POLICY, PROMPT_POLICY, PROVIDER_SELECTION, EXECUTION_CONTROL, CONTEXT_RUNTIME,
                                AGENT_CONTEXT)}
