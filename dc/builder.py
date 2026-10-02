"""Decision Context 빌더 -- State 층과 Policy 층 사이의 계약층.

    State 소스들
        ↓  1. Select    목적이 부른 상태만 읽는다(역할 -> 실체). 소스 · 역할이 없어도 빼지 않고 UNKNOWN 으로 남긴다
        ↓  2. Filter    신선도를 **하나의 '지금'** 으로 다시 잰다. STALE · UNKNOWN 을 **지우지 않는다** -- 표시한다
        ↓  3. Validate  근거(provenance) · 권위(basis) · 일관성(값 집합 · 유효성과 값) · 시각. 못 넘으면 강등(INVALID)
        ↓  4. Project   목적별 키("역할.상태")로 · 목적 순서대로. 제약 · 능력 · 행동(가능/불가능과 까닭)을 붙인다
        ↓  5. Freeze    frozen dataclass + 내용 해시 id. 뒤에 State 가 바뀌어도 이 문맥은 안 바뀐다
    DecisionContext

빌더가 하지 않는 것:
    상태 계산(State 층의 일) · 행동 고르기(Policy 의 일) · 목적함수 갖기(Policy 의 일) · LLM 에게 보일 글 짓기(Context Policy 의 일)
    시계 읽기(‘지금’ 은 인자) · 무작위 · 모르는 값을 추정으로 메우기 · STALE 을 FRESH 로 되돌리기
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import (CONSTRAINT_OPS, FRESH, FUTURE_OBSERVATION, INCOHERENT, INVALID, NO_EVIDENCE, NO_RULE, NO_SOURCE,
                    NOT_APPLICABLE, NOT_SCALAR, OUT_OF_DOMAIN, PERMANENT, SCALAR, SOURCE_ERROR, STALE,
                    STALE_AT_SOURCE, STALE_PURPOSE, STALE_TTL, STATUSES, UNAUTHORIZED_BASIS, UNBOUND_ROLE, UNKNOWN,
                    UNTIMED, UNTIMED_REQUIRED, USABLE, Action, Constraint, Issue, Provenance, StateRecord, StateView,
                    Subject, Validity)
from .purpose import PURPOSES, Purpose, PurposeError, StateRef, is_objective_word
from .snapshot import freeze
from .sources import SourceError, check_source

BUILDER_VERSION = "dc-builder-1"


@dataclass
class _Sel:
    """빌드 중의 상태 하나(가변 -- Freeze 에서 StateView 로 굳는다)."""
    ref: StateRef
    entity: str
    tail: "str | None"
    rec: StateRecord
    value: object = None
    status: str = UNKNOWN
    freshness: str = UNTIMED
    age_ms: "float | None" = None
    ttl_ms: "float | None" = None
    issues: list = field(default_factory=list)

    def flag(self, code, detail="", status=None):
        self.issues.append(Issue(code, detail))
        if status is not None:
            self.status = status


class DecisionContextBuilder:
    def __init__(self, sources=(), purposes: "dict | None" = None):
        self.sources: dict = {}
        self.purposes = dict(PURPOSES if purposes is None else purposes)
        for s in sources:
            self.register(s)

    def register(self, src) -> None:
        check_source(src)
        if src.name in self.sources:
            raise SourceError(f"소스 {src.name} 가 이미 등록되었다")
        self.sources[src.name] = src

    # =========================================================================================================
    def build(self, purpose, subject, *, now_ms, constraints=(), capabilities: "dict | None" = None):
        P = self._purpose(purpose)
        subj = subject if isinstance(subject, Subject) else Subject.of(subject)
        nows = self._nows(P, now_ms)
        sel = self.select(P, subj, nows)
        for s in sel:
            self.filter(s, nows.get(s.ref.source))
        for s in sel:
            self.validate(s)
        states, validity = self.project(P, sel)
        cons = self._constraints(P, constraints)
        caps = self._capabilities(capabilities)
        actions = self._actions(P, dict(caps))
        prov = Provenance(BUILDER_VERSION, P.version,
                          tuple((n, tuple(sorted((k, str(v)) for k, v in self.sources[n].versions().items())))
                                for n in sorted({r.source for r in P.refs}) if n in self.sources))
        return freeze(P.name, tuple(sorted(nows.items())), subj.roles, states, cons, caps, actions, validity, prov)

    # ---- 1. Select ------------------------------------------------------------------------------------------
    def select(self, P: Purpose, subj: Subject, nows: dict) -> "list[_Sel]":
        out = []
        for ref in P.refs:
            ent = subj.get(ref.role)
            if ent is None or ent == ():
                if ent == () and not ref.required:
                    continue        # 펼칠 실체가 하나도 없다(예: 도구를 안 썼다) -- 모르는 상태가 아니라 없는 실체다
                rec = StateRecord(ref.source, "", ref.name, None, UNKNOWN, "OBSERVED", reason="subject 에 역할이 없다")
                out.append(_Sel(ref, "", None, rec, issues=[Issue(UNBOUND_ROLE, ref.role)]))
                continue
            for e, tail in ([(x, x.rsplit(":", 1)[-1]) for x in ent] if isinstance(ent, tuple) else [(ent, None)]):
                out.append(self._read(ref, e, tail, nows.get(ref.source)))
        return out

    def _read(self, ref, entity, tail, now) -> _Sel:
        src = self.sources.get(ref.source)
        if src is None:
            rec = StateRecord(ref.source, entity, ref.name, None, UNKNOWN, "OBSERVED", reason="소스가 없다")
            return _Sel(ref, entity, tail, rec, issues=[Issue(NO_SOURCE, ref.source)])
        try:
            rec = src.read(entity, ref.name, now)
        except Exception as e:   # 소스가 터진 것도 '모른다' 이다 -- 추정으로 메우지 않는다
            rec = StateRecord(ref.source, entity, ref.name, None, UNKNOWN, "OBSERVED", reason="소스 예외")
            return _Sel(ref, entity, tail, rec, issues=[Issue(SOURCE_ERROR, f"{type(e).__name__}: {e}")])
        if (rec.entity, rec.name, rec.source) != (entity, ref.name, ref.source):
            bad = StateRecord(ref.source, entity, ref.name, None, UNKNOWN, "OBSERVED", reason="소스가 다른 상태를 줬다")
            return _Sel(ref, entity, tail, bad,
                        issues=[Issue(SOURCE_ERROR, f"물은 것 {entity}.{ref.name}, 받은 것 {rec.entity}.{rec.name}")])
        return _Sel(ref, entity, tail, rec)

    # ---- 2. Filter(신선도) -----------------------------------------------------------------------------------
    @staticmethod
    def filter(s: _Sel, now) -> None:
        r = s.rec
        s.value = r.value
        s.status = r.status if r.status in STATUSES else INVALID
        if r.status not in STATUSES:
            s.flag(INCOHERENT, f"모르는 유효성 {r.status!r}")
        if not isinstance(s.value, SCALAR):
            s.flag(NOT_SCALAR, type(s.value).__name__, INVALID)
            s.value = None
        claims = s.status in USABLE      # 소스가 '지금 값' 이라 주장하나
        ttls = [t for t in (r.ttl_ms, s.ref.max_age_ms) if t is not None]
        s.ttl_ms = min(ttls) if ttls else None
        if r.observed_at_ms is not None and now is not None:
            s.age_ms = now - r.observed_at_ms
            if s.age_ms < 0:
                s.flag(FUTURE_OBSERVATION, f"관측 {r.observed_at_ms} > 지금 {now}", INVALID if claims else None)
        if r.permanent:
            s.freshness, s.ttl_ms = PERMANENT, None
        elif s.age_ms is None:
            s.freshness = UNTIMED
            if s.ref.max_age_ms is not None and claims:
                s.flag(UNTIMED_REQUIRED, f"목적이 max_age {s.ref.max_age_ms}ms 를 요구 -- 신선도를 증명할 수 없다", UNKNOWN)
        elif s.age_ms >= 0:
            s.freshness = FRESH
            if s.ttl_ms is not None and s.age_ms > s.ttl_ms:
                s.freshness = STALE
                by_purpose = s.ref.max_age_ms is not None and s.age_ms > s.ref.max_age_ms and \
                    (r.ttl_ms is None or s.age_ms <= r.ttl_ms)
                s.flag(STALE_PURPOSE if by_purpose else STALE_TTL, f"나이 {s.age_ms:.0f}ms > {s.ttl_ms:.0f}ms",
                       STALE if s.status in USABLE else None)
        if r.status == STALE and s.freshness != STALE:
            # 소스가 STALE 이라 한 것을 DC 의 '지금' 이 더 이르다고 FRESH 로 되돌리지 않는다
            s.freshness = STALE
            s.flag(STALE_AT_SOURCE, "소스가 이미 STALE")

    # ---- 3. Validate ----------------------------------------------------------------------------------------
    def validate(self, s: _Sel) -> None:
        r = s.rec
        claims = r.status in USABLE or r.status == STALE      # 값이 있다고 주장한 것만 근거를 본다
        if s.value is not None:
            dom = s.ref.values
            if dom is None and s.ref.source in self.sources:
                try:
                    dom = self.sources[s.ref.source].domain(s.entity, s.ref.name)
                except Exception:
                    dom = None
            if dom is not None and s.value not in dom:
                s.flag(OUT_OF_DOMAIN, f"{s.value!r} ∉ {tuple(dom)}", INVALID)
        if s.status in USABLE and s.value is None:
            s.flag(INCOHERENT, f"{s.status} 인데 값이 없다", INVALID)
        if not claims:
            return
        if r.basis not in s.ref.allowed_basis:
            s.flag(UNAUTHORIZED_BASIS, f"{r.basis} 는 이 목적이 받지 않는다", INVALID)
        if not r.rule_id:
            s.flag(NO_RULE, "어느 규칙이 이 값을 냈는지 모른다", INVALID)
        if not r.evidence_refs:
            s.flag(NO_EVIDENCE, "근거 참조가 없다 -- 되짚을 수 없는 상태는 받지 않는다", INVALID)

    # ---- 4. Project -----------------------------------------------------------------------------------------
    @staticmethod
    def project(P: Purpose, sel: "list[_Sel]"):
        views = []
        for s in sel:
            r = s.rec
            views.append(StateView(
                key=s.ref.key(s.tail), role=s.ref.role, entity=s.entity, name=s.ref.name, source=s.ref.source,
                required=s.ref.required, value=s.value, status=s.status, source_status=r.status,
                freshness=s.freshness, age_ms=s.age_ms, ttl_ms=s.ttl_ms, observed_at_ms=r.observed_at_ms,
                basis=r.basis, rule_id=r.rule_id, rule_version=r.rule_version, evidence_refs=tuple(r.evidence_refs),
                reason=r.reason, issues=tuple(s.issues)))
        usable = tuple(v.key for v in views if v.usable)
        unc = tuple(f"{v.key}={v.status}" for v in views if v.status in (UNKNOWN, STALE, INVALID))
        na = tuple(v.key for v in views if v.status == NOT_APPLICABLE)
        missing = tuple(v.key for v in views if v.required and not v.usable and v.status != NOT_APPLICABLE)
        rejected = tuple(f"{v.key}:{','.join(i.code for i in v.issues)}" for v in views
                         if v.issues and v.status != v.source_status)
        return views, Validity(complete=not missing, usable=usable, uncertain=unc, not_applicable=na,
                               missing_required=missing, rejected=rejected)

    # ---- 보조 -----------------------------------------------------------------------------------------------
    def _purpose(self, purpose) -> Purpose:
        if isinstance(purpose, Purpose):
            return purpose
        if purpose not in self.purposes:
            raise PurposeError(f"모르는 목적 {purpose!r} -- {sorted(self.purposes)}")
        return self.purposes[purpose]

    def _nows(self, P: Purpose, now_ms) -> dict:
        names = sorted({r.source for r in P.refs})
        if isinstance(now_ms, dict):
            lack = [n for n in names if n in self.sources and n not in now_ms]
            if lack:
                raise ValueError(f"소스 {lack} 의 '지금' 이 없다(소스마다 시각 기준이 다를 수 있다)")
            out = {n: now_ms[n] for n in names if n in now_ms}
        elif isinstance(now_ms, (int, float)) and not isinstance(now_ms, bool):
            out = {n: now_ms for n in names}
        else:
            raise TypeError("now_ms 는 수 또는 {소스: 수} -- 빌더는 시계를 읽지 않는다")
        return {k: float(v) for k, v in out.items()}

    @staticmethod
    def _constraints(P: Purpose, constraints) -> tuple:
        out = []
        for c in constraints:
            c = c if isinstance(c, Constraint) else Constraint.from_dict(c)
            if is_objective_word(c.name):
                raise PurposeError(f"제약 {c.name!r}: 목적함수 · 선호는 결정 문맥에 들어오지 않는다 -- 정책이 가진다")
            if c.name not in P.constraints:
                raise PurposeError(f"목적 {P.name} 은 제약 {c.name!r} 를 받지 않는다 -- {P.constraints}")
            if c.op not in CONSTRAINT_OPS:
                raise PurposeError(f"제약 {c.name}: 모르는 연산 {c.op!r}")
            vals = c.value if (c.op == "in" and isinstance(c.value, tuple)) else (c.value,)
            if c.op == "in" and not isinstance(c.value, tuple):
                raise PurposeError(f"제약 {c.name}: in 은 tuple 값")
            if not all(isinstance(v, SCALAR) for v in vals):
                raise PurposeError(f"제약 {c.name}: 값은 스칼라(또는 스칼라 tuple)")
            out.append(c)
        return tuple(sorted(out, key=lambda c: (c.name, c.op)))

    @staticmethod
    def _capabilities(capabilities) -> tuple:
        caps = dict(capabilities or {})
        for k, v in caps.items():
            if not isinstance(v, SCALAR):
                raise TypeError(f"능력 {k}: 값은 스칼라")
        return tuple(sorted(caps.items()))

    @staticmethod
    def _actions(P: Purpose, caps: dict) -> tuple:
        out = []
        for a in P.actions:
            missing = tuple(r for r in a.requires if not caps.get(r))
            out.append(Action(a.name, not missing, tuple(a.requires), missing, a.meaning))
        return tuple(out)
