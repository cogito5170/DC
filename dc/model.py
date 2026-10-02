"""Decision Context 의 형(型). 전부 frozen dataclass 이고 모음은 tuple 이다 -- 만든 뒤 바꿀 수 없다.

저장하는 것은 둘뿐이다(baseline BD-08 · PC-07 · SCHEMA §4.1):

    Core             정책이 읽는 것. 목적@판본 · as_of · subject · 키별 [값(쓸 수 있을 때만), 유효성] · 제약 · 가능 행동 이름
    Provenance       감사 · 재현 · explain. 키별 근거 종류 · 규칙@판 · 근거 참조 · 관측 시각 · 소스 유효성 · 문제 · 막힌 값,
                     능력(입력 기록) · 못 하는 행동의 모자란 능력 · 빌더/소스 판본
    DecisionContext  digest(core + provenance 전부의 sha256) + 위 둘. id = "dc-" + digest 앞 16 자(투영)

투영(저장하지 않는다, `dc/project.py`): StateView(나이 · 신선도 · 필수 여부를 붙인 상태 하나) · Validity · Action 목록.
`reason` 은 DC 에 없다 -- 원 수치가 새어 들고(I1) 크기가 core 만큼이다(SCHEMA §4.4). 사람이 읽을 까닭은 소스의 explain 에서.

    StateRecord      소스(State 층)가 준 상태 하나. DC 는 이것을 **계산하지 않는다** -- 받아서 검사할 뿐이다
    Constraint       단단한 제약(정책이 넘으면 안 되는 선). 목적함수가 아니다

상태 값의 유효성 이름은 Sensor(llmsensor.state.Status)와 같다. 소스 쪽 import 는 하지 않는다 -- 문자열로만 맞춘다.
"""
from __future__ import annotations

import json
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
    observed_at_ms: "float | None" = None
    ttl_ms: "float | None" = None
    permanent: bool = False              # 끝난 일에 대한 사실 -- 낡지 않는다
    since_ms: "float | None" = None
    time_base: "str | None" = None       # unix_ms · monotonic_ms · None(모름) -- baseline BD-33
    local: "str | None" = None           # 여러 실체 역할에서 이 실체의 지역 이름(소스가 가른 것, BD-32). 키 `역할[지역].이름` 에 쓴다.
                                         # 없으면 빌더가 id 의 마지막 `:` 뒤로 가른다(도구 이름처럼 `:` 가 없을 때만 맞다)


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


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
    """투영 -- core 의 유효성과 목적 명세(필수 여부)에서 다시 계산한다. 저장하지 않는다."""
    complete: bool               # 필수 상태가 전부 판정되었나(쓸 수 있음 또는 NOT_APPLICABLE)
    usable: tuple = ()           # 키
    uncertain: tuple = ()        # "키=유효성" -- UNKNOWN · STALE · INVALID
    not_applicable: tuple = ()   # 키
    missing_required: tuple = ()  # 판정 안 된 필수 키
    rejected: tuple = ()         # "키:문제" -- DC 가 거절(강등)한 것

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


# -- 저장되는 것: core ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CoreState:
    key: str                     # "<역할>.<상태>" 또는 "<역할>[<꼬리>].<상태>"
    value: object                # 쓸 수 있을 때만(또는 목적이 그 키에 allow_stale 을 선언했고 STALE 일 때). 아니면 None
    status: str                  # DC 판정 뒤 유효성


@dataclass(frozen=True)
class CoreRow:
    """질의 결과의 행 하나(baseline BD-26 · PC-23) -- 상태와 같은 규칙: 속성 값은 쓸 수 있을 때만(또는 질의가 allow_stale 을 선언했고 STALE)."""
    id: str
    model: str
    props: tuple                 # ((속성, 값 | None, 유효성), ...) 속성 이름 순
    must: bool = False
    edges: tuple = ()            # ((관계, 출발, 도착), ...) -- 양끝이 결과 안에 있는 것만

    def to_dict(self) -> dict:
        return {"id": self.id, "model": self.model, "props": {k: [v, st] for k, v, st in self.props},
                "must": self.must, "edges": [list(e) for e in self.edges]}

    @classmethod
    def from_dict(cls, d: dict) -> "CoreRow":
        return cls(d["id"], d["model"], tuple((k, v[0], v[1]) for k, v in sorted(d["props"].items())), d["must"],
                   tuple(tuple(e) for e in d["edges"]))


@dataclass(frozen=True)
class CoreQuery:
    name: str
    rows: tuple                  # CoreRow, 소스가 준 순서(질의의 order_by)
    matched: int                 # limit 전 개수
    priority: int = 0
    droppable: str = "[]"        # 질의의 droppable 술어(맥락 정책이 쓴다) -- 정준 JSON 문자열로 그대로 옮긴다(바꿀 수 없게)

    def to_dict(self) -> dict:
        return {"rows": [r.to_dict() for r in self.rows], "matched": self.matched, "priority": self.priority,
                "droppable": json.loads(self.droppable)}

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "CoreQuery":
        return cls(name, tuple(CoreRow.from_dict(r) for r in d["rows"]), d["matched"], d["priority"],
                   json.dumps(d["droppable"], sort_keys=True, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True)
class Core:
    purpose: str
    purpose_version: str
    as_of: tuple                 # ((소스, 지금 ms), ...) -- 빌더에 준 '지금'. 빌더는 시계를 읽지 않는다
    subject: tuple               # ((역할, 실체 또는 실체 tuple), ...)
    states: tuple                # CoreState, 목적 명세의 순서
    constraints: tuple           # Constraint
    actions: tuple               # 가능한 행동 이름
    queries: tuple = ()          # CoreQuery, 이름 순 (PC-23)
    default_action: "str | None" = None   # 목적의 안전 기본 결정을 능력으로 고른 것(BD-23 · BD-76). 없으면 None

    def to_dict(self) -> dict:
        return {"purpose": self.purpose, "purpose_version": self.purpose_version,
                "default_action": self.default_action,
                "queries": {q.name: q.to_dict() for q in self.queries},
                "as_of": {k: v for k, v in self.as_of},
                "subject": {r: (list(e) if isinstance(e, tuple) else e) for r, e in self.subject},
                "states": {s.key: [s.value, s.status] for s in self.states},
                "constraints": [c.to_dict() for c in self.constraints], "actions": list(self.actions)}

    @classmethod
    def from_dict(cls, d: dict) -> "Core":
        return cls(d["purpose"], d["purpose_version"], tuple(sorted(d["as_of"].items())),
                   tuple((r, tuple(e) if isinstance(e, list) else e) for r, e in sorted(d["subject"].items())),
                   tuple(CoreState(k, v[0], v[1]) for k, v in sorted(d["states"].items())),
                   tuple(Constraint.from_dict(c) for c in d["constraints"]), tuple(d["actions"]),
                   tuple(CoreQuery.from_dict(n, q) for n, q in sorted(d.get("queries", {}).items())),
                   d.get("default_action"))


# -- 저장되는 것: provenance ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StateProvenance:
    key: str
    role: str
    entity: str
    name: str
    source: str
    basis: str
    rule_id: str
    rule_version: "str | int | None"
    evidence_refs: tuple         # 참조만 -- 사슬을 복사하지 않는다(BD-06)
    observed_at_ms: "float | None"
    ttl_ms: "float | None"       # 실제로 적용한 TTL(소스 TTL 과 목적 max_age 중 엄한 것) -- 입력 기록
    permanent: bool
    source_status: str
    issues: tuple = ()           # Issue
    withheld: object = None      # core 에 싣지 않은 소스 값(STALE · INVALID 등) -- 설명용. core 값이 있으면 None
    time_base: "str | None" = None   # 관측 시각의 기준(BD-33)

    def to_dict(self) -> dict:
        return {"key": self.key, "role": self.role, "entity": self.entity, "name": self.name, "source": self.source,
                "time_base": self.time_base,
                "basis": self.basis, "rule_id": self.rule_id, "rule_version": self.rule_version,
                "evidence_refs": list(self.evidence_refs), "observed_at_ms": self.observed_at_ms,
                "ttl_ms": self.ttl_ms, "permanent": self.permanent, "source_status": self.source_status,
                "issues": [i.to_dict() for i in self.issues], "withheld": self.withheld}

    @classmethod
    def from_dict(cls, d: dict) -> "StateProvenance":
        return cls(**{**d, "evidence_refs": tuple(d["evidence_refs"]),
                      "issues": tuple(Issue(**i) for i in d["issues"])})


@dataclass(frozen=True)
class QueryProvenance:
    name: str
    source: str
    spec: str                    # 질의 명세의 정준 JSON(재현용)
    refs: tuple = ()             # ((행 id, 속성, 근거 참조, 관측 ms, 소스 유효성), ...) -- 참조만, 값은 없다
    withheld: tuple = ()         # ((행 id, 속성, 소스 값), ...) -- core 에 싣지 않은 값(설명용)
    issues: tuple = ()           # Issue
    allow_stale: bool = False    # 이 질의가 낡은 값을 core 에 싣겠다고 선언했나(BD-65 · CMD-D13). 명세(spec)는 소스에 가는 칸만이라 따로 둔다

    def to_dict(self) -> dict:
        return {"name": self.name, "source": self.source, "spec": self.spec, "allow_stale": self.allow_stale,
                "refs": [list(r) for r in self.refs],
                "withheld": [list(w) for w in self.withheld], "issues": [i.to_dict() for i in self.issues]}

    @classmethod
    def from_dict(cls, d: dict) -> "QueryProvenance":
        return cls(d["name"], d["source"], d["spec"], tuple(tuple(r) for r in d["refs"]),
                   tuple(tuple(w) for w in d["withheld"]), tuple(Issue(**i) for i in d["issues"]),
                   bool(d.get("allow_stale", False)))


@dataclass(frozen=True)
class Provenance:
    builder: str                 # DC 빌더 판본
    sources: tuple = ()          # ((소스 이름, ((판본 키, 값), ...)), ...)
    capabilities: tuple = ()     # ((이름, 값), ...) -- 가능 행동을 정한 입력
    missing: tuple = ()          # ((못 하는 행동, (모자란 능력, ...)), ...)
    states: tuple = ()           # StateProvenance, core.states 와 같은 순서
    queries: tuple = ()          # QueryProvenance, 이름 순

    def to_dict(self) -> dict:
        return {"builder": self.builder, "sources": {n: dict(v) for n, v in self.sources},
                "capabilities": {k: v for k, v in self.capabilities},
                "missing": {a: list(m) for a, m in self.missing},
                "states": [s.to_dict() for s in self.states], "queries": [q.to_dict() for q in self.queries]}

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(d["builder"], tuple((n, tuple(sorted(v.items()))) for n, v in sorted(d["sources"].items())),
                   tuple(sorted(d["capabilities"].items())), tuple((a, tuple(m)) for a, m in sorted(d["missing"].items())),
                   tuple(sorted((StateProvenance.from_dict(s) for s in d["states"]), key=lambda s: s.key)),
                   tuple(QueryProvenance.from_dict(q) for q in d.get("queries", [])))

    def state(self, key: str) -> StateProvenance:
        for s in self.states:
            if s.key == key:
                return s
        raise KeyError(key)


@dataclass(frozen=True)
class DecisionContext:
    """이번 결정을 위해 알아야 할 것의 고정 스냅숏 -- 저장하는 것은 digest · core · provenance 셋뿐이다.

    여기에 없는 것(일부러): 목적함수 · 가중치 · 선호(정책이 가진다), 고른 행동(정책이 고른다),
    원 텔레메트리 · 지표 값 · `reason`(State 층 밖), LLM 에게 보일 글(LLM Context 는 이 다음 단계다).
    """
    digest: str                  # core + provenance 의 정준 JSON 의 sha256
    core: Core
    provenance: Provenance
    spec: object = field(default=None, compare=False, repr=False)   # 지은 목적 명세(투영용, 해시 · 직렬화 밖)

    # -- core 읽기(정책) --------------------------------------------------------------------------------------
    @property
    def id(self) -> str:
        return "dc-" + self.digest[:16]

    @property
    def purpose(self) -> str:
        return self.core.purpose

    @property
    def as_of(self) -> tuple:
        return self.core.as_of

    @property
    def subject(self) -> tuple:
        return self.core.subject

    @property
    def constraints(self) -> tuple:
        return self.core.constraints

    @property
    def available_actions(self) -> tuple:
        return self.core.actions

    @property
    def default_action(self) -> "str | None":
        """목적의 안전 기본 결정 -- 정책이 필수 상태를 몰라 규칙을 정할 수 없을 때 쓴다(BD-76). 가능한 행동 가운데서만 고른다."""
        return self.core.default_action

    def keys(self) -> tuple:
        return tuple(s.key for s in self.core.states)

    def _core_state(self, key: str) -> CoreState:
        for s in self.core.states:
            if s.key == key:
                return s
        raise KeyError(key)

    def status(self, key: str) -> str:
        return self._core_state(key).status

    def value(self, key: str, allow_stale: bool = False):
        """쓸 수 있을 때만 값, 아니면 None(=모름).

        STALE 값은 **목적 명세가 그 키에 allow_stale 을 선언했을 때만** core 에 실린다(CMD-D6). 그때도 정책이
        allow_stale=True 로 부를 때만 내준다 -- 명세와 정책 둘 다 명시해야 낡은 값이 쓰인다. UNKNOWN · INVALID 는 늘 None."""
        s = self._core_state(key)
        if s.status in USABLE or (allow_stale and s.status == STALE):
            return s.value
        return None

    def query(self, name: str) -> "CoreQuery":
        for q in self.core.queries:
            if q.name == name:
                return q
        raise KeyError(name)

    def rows(self, name: str) -> list:
        """질의 결과를 맥락에 쓰기 좋은 꼴로: [{"id", "model", <속성>: 값 | None, "_unusable": [...], "_stale": [...], "_edges": [...]}].
        쓸 수 없는 속성은 값이 None 이고 `_unusable` 에 이름이 실린다 -- 모르는 값을 지어내지 않는다. 질의가 allow_stale 을 선언해
        실린 낡은 값은 `_unusable` 과 함께 `_stale` 에도 실린다(MS 질의의 표시와 같은 이름)."""
        out = []
        for r in self.query(name).rows:
            d = {"id": r.id, "model": r.model}
            for k, v, st in r.props:
                d[k] = v
                if st not in USABLE:
                    d.setdefault("_unusable", []).append(k)
                if st == STALE and v is not None:
                    d.setdefault("_stale", []).append(k)
            if r.edges:
                d["_edges"] = [list(e) for e in r.edges]
            out.append(d)
        return out

    @property
    def reuse_key(self) -> str:
        """결정 재사용 열쇠(BD-37) -- as_of 를 뺀 core 의 해시. 상태 값 · 유효성 · 제약 · 가능 행동이 같으면 같다."""
        from .snapshot import digest_of
        d = self.core.to_dict()
        d.pop("as_of")
        return "rk-" + digest_of(d)[:16]

    # -- 투영(저장하지 않는다) ------------------------------------------------------------------------------
    def state(self, key: str):
        from .project import view
        return view(self, key)

    @property
    def states(self) -> tuple:
        from .project import views
        return views(self)

    @property
    def validity(self) -> "Validity":
        from .project import validity
        return validity(self)

    @property
    def actions(self) -> tuple:
        from .project import actions
        return actions(self)

    @property
    def capabilities(self) -> tuple:
        return self.provenance.capabilities

    # -- 직렬화 -----------------------------------------------------------------------------------------------
    def body(self) -> dict:
        return {"core": self.core.to_dict(), "provenance": self.provenance.to_dict()}

    def to_dict(self) -> dict:
        return {"digest": self.digest, **self.body()}

    def core_dict(self) -> dict:
        """정책 쪽으로 보내는 것(SCHEMA §4.3) -- id 와 core 만."""
        return {"id": self.id, **self.core.to_dict()}

    def verify(self) -> bool:
        """내용이 digest 와 맞나 -- 만든 뒤 누가 (object.__setattr__ 로라도) 고쳤으면 False."""
        from .snapshot import digest_of
        return digest_of(self.body()) == self.digest


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
