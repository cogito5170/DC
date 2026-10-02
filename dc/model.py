"""Decision Context 의 형(型). 전부 frozen dataclass 이고 모음은 tuple 이다 -- 만든 뒤 바꿀 수 없다.

    StateRecord      소스(State 층)가 준 상태 하나. DC 는 이것을 **계산하지 않는다** -- 받아서 검사할 뿐이다
    StateView        결정 문맥 안의 상태 하나. 값 + 유효성 + 신선도 + 근거 + DC 가 찾은 문제(issues)
    Constraint       단단한 제약(정책이 넘으면 안 되는 선). 목적함수가 아니다
    Action           이번 결정에서 구조적으로 실행 가능한 행동. **고르는 것은 정책**이다
    Validity         문맥 전체의 판정: 완결인가, 무엇이 불확실한가, 무엇을 거절했나
    Provenance       어느 소스 · 어느 판본 · 어느 목적 명세 · 어느 빌더에서 나왔나
    DecisionContext  위를 묶은 고정 스냅숏. id 는 내용의 해시다(같은 입력 -> 같은 id)

상태 값의 유효성 이름은 Sensor(llmsensor.state.Status)와 같다. 소스 쪽 import 는 하지 않는다 -- 문자열로만 맞춘다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# -- 유효성 · 신선도 · 근거 이름(Sensor 와 같은 낱말) -------------------------------------------------------------
OBSERVED, DERIVED, INFERRED = "OBSERVED", "DERIVED", "INFERRED"
UNKNOWN, STALE, INVALID, NOT_APPLICABLE = "UNKNOWN", "STALE", "INVALID", "NOT_APPLICABLE"
STATUSES = (OBSERVED, DERIVED, INFERRED, UNKNOWN, STALE, INVALID, NOT_APPLICABLE)
USABLE = frozenset((OBSERVED, DERIVED, INFERRED))       # 정책이 '지금 값' 으로 쓸 수 있는 것은 이 셋뿐

FRESH, UNTIMED, PERMANENT = "FRESH", "UNTIMED", "PERMANENT"
FRESHNESS = (FRESH, STALE, UNTIMED, PERMANENT)

# 근거 종류 -- Sensor llmsensor.state.Basis 8 개와 같다(baseline PC-14 · DUP-13). 모르는 종류는 UNAUTHORIZED_BASIS 로 거절한다
BASES = ("OBSERVED", "DEFINITIONAL", "RUNTIME_DECLARED", "OPERATOR_ASSUMED", "ESTIMATE", "PROVIDER_DECLARED",
         "VALIDATED_EXPERIMENT", "EXTERNAL_LABEL")
DEFAULT_ALLOWED_BASIS = tuple(b for b in BASES if b != "ESTIMATE")   # ESTIMATE 는 권위가 없다

SCALAR = (str, int, float, bool, type(None))

# -- DC 가 상태에 붙이는 문제 이름 -------------------------------------------------------------------------------
STALE_TTL = "STALE_TTL"                  # 소스 TTL 을 넘겼다
STALE_PURPOSE = "STALE_PURPOSE"          # 목적이 요구한 max_age 를 넘겼다(소스 TTL 보다 엄할 수 있다)
STALE_AT_SOURCE = "STALE_AT_SOURCE"      # 소스가 이미 STALE 이라 했다 -- DC 가 FRESH 로 되돌리지 않는다
UNTIMED_REQUIRED = "UNTIMED_REQUIRED"    # 목적이 신선도를 요구했는데 근거에 시각이 없다
FUTURE_OBSERVATION = "FUTURE_OBSERVATION"  # 관측 시각이 '지금' 보다 뒤 -- 시계가 어긋났다
NO_EVIDENCE = "NO_EVIDENCE"              # 쓸 수 있다는 상태에 근거 참조가 없다
NO_RULE = "NO_RULE"                      # 쓸 수 있다는 상태에 규칙 id 가 없다
UNAUTHORIZED_BASIS = "UNAUTHORIZED_BASIS"  # 이 목적이 받지 않는 근거 종류(기본: ESTIMATE)
OUT_OF_DOMAIN = "OUT_OF_DOMAIN"          # 정의된 값 집합 밖의 값
NOT_SCALAR = "NOT_SCALAR"                # 값이 스칼라가 아니다(원 텔레메트리 덩어리를 막는다)
INCOHERENT = "INCOHERENT"                # 유효성과 값이 맞지 않는다(쓸 수 있다는데 값이 없다 등)
NO_SOURCE = "NO_SOURCE"                  # 그 이름의 소스가 등록되지 않았다
SOURCE_ERROR = "SOURCE_ERROR"            # 소스가 읽다가 예외를 냈다
UNBOUND_ROLE = "UNBOUND_ROLE"            # subject 에 그 역할의 실체가 없다


@dataclass(frozen=True)
class StateRecord:
    """소스가 DC 에 건네는 상태 하나. 시각은 전부 ms(epoch 든 실행 시작 기준이든 한 소스 안에서 같은 기준)."""
    source: str
    entity: str
    name: str
    value: object
    status: str
    basis: str
    rule_id: str = ""
    rule_version: "str | int | None" = None
    evidence_refs: tuple = ()
    reason: str = ""
    observed_at_ms: "float | None" = None
    ttl_ms: "float | None" = None
    permanent: bool = False              # 끝난 일에 대한 사실 -- 낡지 않는다
    since_ms: "float | None" = None


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class StateView:
    key: str                     # 문맥 안 이름: "<역할>.<상태>" 또는 "<역할>[<꼬리>].<상태>"
    role: str
    entity: str
    name: str
    source: str
    required: bool
    value: object                # 소스가 준 값 그대로(STALE · INVALID 여도 남긴다 -- 설명을 위해). 쓸지는 usable 이 정한다
    status: str                  # DC 의 판정 뒤 유효성
    source_status: str           # 소스가 말한 유효성
    freshness: str
    age_ms: "float | None"
    ttl_ms: "float | None"       # 실제로 적용한 TTL(소스 TTL 과 목적 max_age 중 엄한 것)
    observed_at_ms: "float | None"
    basis: str
    rule_id: str
    rule_version: "str | int | None"
    evidence_refs: tuple
    reason: str
    issues: tuple = ()           # Issue

    @property
    def usable(self) -> bool:
        return self.status in USABLE

    def to_dict(self) -> dict:
        return {"key": self.key, "role": self.role, "entity": self.entity, "name": self.name, "source": self.source,
                "required": self.required, "value": self.value, "status": self.status,
                "source_status": self.source_status, "freshness": self.freshness, "age_ms": self.age_ms,
                "ttl_ms": self.ttl_ms, "observed_at_ms": self.observed_at_ms, "basis": self.basis,
                "rule_id": self.rule_id, "rule_version": self.rule_version, "evidence_refs": list(self.evidence_refs),
                "reason": self.reason, "issues": [i.to_dict() for i in self.issues]}

    @classmethod
    def from_dict(cls, d: dict) -> "StateView":
        return cls(**{**d, "evidence_refs": tuple(d["evidence_refs"]),
                      "issues": tuple(Issue(**i) for i in d["issues"])})


CONSTRAINT_OPS = ("<=", ">=", "==", "in")


@dataclass(frozen=True)
class Constraint:
    """넘으면 안 되는 선. '무엇을 더 원하나'(목적함수)는 여기 들어오지 않는다 -- 정책이 가진다."""
    name: str
    op: str
    value: object
    source: str = "operator"     # 누가 걸었나(operator · request · tenant ...)

    def to_dict(self) -> dict:
        v = list(self.value) if isinstance(self.value, tuple) else self.value
        return {"name": self.name, "op": self.op, "value": v, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> "Constraint":
        v = tuple(d["value"]) if isinstance(d["value"], list) else d["value"]
        return cls(d["name"], d["op"], v, d.get("source", "operator"))


@dataclass(frozen=True)
class Action:
    name: str
    available: bool
    requires: tuple = ()         # 필요한 능력 이름
    missing: tuple = ()          # 없어서 못 하는 능력 -- available=False 일 때만
    meaning: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "available": self.available, "requires": list(self.requires),
                "missing": list(self.missing), "meaning": self.meaning}

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        return cls(d["name"], d["available"], tuple(d["requires"]), tuple(d["missing"]), d.get("meaning", ""))


@dataclass(frozen=True)
class Validity:
    complete: bool               # 필수 상태가 전부 판정되었나(쓸 수 있음 또는 NOT_APPLICABLE)
    usable: tuple = ()           # 키
    uncertain: tuple = ()        # "키=유효성" -- UNKNOWN · STALE · INVALID
    not_applicable: tuple = ()   # 키
    missing_required: tuple = ()  # 판정 안 된 필수 키
    rejected: tuple = ()         # "키:문제" -- DC 가 거절(강등)한 것

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Validity":
        return cls(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in d.items()})


@dataclass(frozen=True)
class Provenance:
    builder: str                 # DC 빌더 판본
    purpose_version: str         # 목적 명세 판본
    sources: tuple = ()          # ((소스 이름, ((판본 키, 값), ...)), ...)

    def to_dict(self) -> dict:
        return {"builder": self.builder, "purpose_version": self.purpose_version,
                "sources": {n: dict(v) for n, v in self.sources}}

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(d["builder"], d["purpose_version"],
                   tuple((n, tuple(sorted(v.items()))) for n, v in sorted(d["sources"].items())))


@dataclass(frozen=True)
class DecisionContext:
    """이번 결정을 위해 알아야 할 것의 고정 스냅숏.

    여기에 없는 것(일부러): 목적함수 · 가중치 · 선호(정책이 가진다), 고른 행동(정책이 고른다),
    원 텔레메트리 · 지표 값(State 층 밖), LLM 에게 보일 글(LLM Context 는 이 다음 단계다).
    """
    id: str                      # "dc-" + digest 앞 16 자
    digest: str                  # 아래 칸 전부의 정준 JSON 의 sha256
    purpose: str
    as_of: tuple                 # ((소스, 지금 ms), ...) -- 빌더에 준 '지금'. 빌더는 시계를 읽지 않는다
    subject: tuple               # ((역할, 실체 또는 실체 tuple), ...)
    states: tuple                # StateView
    constraints: tuple           # Constraint
    capabilities: tuple          # ((이름, 값), ...)
    actions: tuple               # Action(가능 · 불가능 모두, 불가능은 까닭과 함께)
    validity: Validity
    provenance: Provenance

    # -- 읽기 -------------------------------------------------------------------------------------------------
    def state(self, key: str) -> StateView:
        for s in self.states:
            if s.key == key:
                return s
        raise KeyError(key)

    def keys(self) -> tuple:
        return tuple(s.key for s in self.states)

    def value(self, key: str, allow_stale: bool = False):
        """쓸 수 있을 때만 값, 아니면 None(=모름). STALE · INVALID 값을 '지금 값' 으로 내주지 않는다.

        allow_stale=True 는 **정책이 명시적으로** 낡은 값을 받겠다고 할 때만 쓴다(Sensor decision/context 에서 옮겨 옴, BD-05).
        그때도 STALE 만 풀린다 -- UNKNOWN · INVALID · NOT_APPLICABLE 은 여전히 None 이다. 문맥 자체는 바뀌지 않는다."""
        s = self.state(key)
        if s.usable or (allow_stale and s.status == STALE):
            return s.value
        return None

    @property
    def available_actions(self) -> tuple:
        return tuple(a.name for a in self.actions if a.available)

    # -- 직렬화 -----------------------------------------------------------------------------------------------
    def body(self) -> dict:
        """digest 를 뺀 내용. 해시는 이것의 정준 JSON 위에서 계산한다."""
        return {"purpose": self.purpose, "as_of": {k: v for k, v in self.as_of},
                "subject": {r: (list(e) if isinstance(e, tuple) else e) for r, e in self.subject},
                "states": [s.to_dict() for s in self.states],
                "constraints": [c.to_dict() for c in self.constraints],
                "capabilities": {k: v for k, v in self.capabilities},
                "actions": [a.to_dict() for a in self.actions],
                "validity": self.validity.to_dict(), "provenance": self.provenance.to_dict()}

    def to_dict(self) -> dict:
        return {"id": self.id, "digest": self.digest, **self.body()}

    def verify(self) -> bool:
        """내용이 digest 와 맞나 -- 만든 뒤 누가 (object.__setattr__ 로라도) 고쳤으면 False."""
        from .snapshot import digest_of
        return digest_of(self.body()) == self.digest and self.id == "dc-" + self.digest[:16]


@dataclass(frozen=True)
class Subject:
    """역할 -> 실체. 실체 하나(str) 또는 여럿(tuple, 예: 도구마다)."""
    roles: tuple = field(default_factory=tuple)

    @classmethod
    def of(cls, mapping: dict) -> "Subject":
        out = []
        for r, e in sorted(mapping.items()):
            if isinstance(e, (list, tuple)):
                e = tuple(sorted(e))
            elif not isinstance(e, str):
                raise TypeError(f"subject 의 {r}: 실체 id 는 str 또는 str 들이어야 한다")
            out.append((r, e))
        return cls(tuple(out))

    def get(self, role):
        for r, e in self.roles:
            if r == role:
                return e
        return None
