"""투영 -- 저장하지 않고 core + provenance + 목적 명세에서 다시 계산하는 칸(baseline SCHEMA §4.1).

    view(ctx, key) -> StateView      상태 하나: core 값 · 유효성 + 근거 + 나이 · 신선도 · 필수 여부
    views(ctx)     -> (StateView, …)
    validity(ctx)  -> Validity       complete · usable · uncertain · not_applicable · missing_required · rejected
    actions(ctx)   -> (Action, …)    가능(core) + 못 함(provenance.missing) -- 목적 명세의 순서로

필수 여부 · 행동의 뜻은 목적 명세에 있다. 문맥이 지어질 때의 명세(`ctx.spec`)를 쓰고, 기록에서 되살린 문맥이면 등록된 목적에서
**이름과 판본이 둘 다 맞는 것**을 찾는다. 판본이 다른 명세로는 투영하지 않는다(추측하지 않는다).
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import (FRESH, FUTURE_OBSERVATION, NOT_APPLICABLE, PERMANENT, STALE, STALE_AT_SOURCE, UNTIMED, UNKNOWN,
                    INVALID, USABLE, Action, Validity)


@dataclass(frozen=True)
class StateView:
    key: str
    role: str
    entity: str
    name: str
    source: str
    required: bool
    value: object                # core 값 -- 쓸 수 있을 때만(또는 선언된 allow_stale 의 STALE). 아니면 None
    status: str
    source_status: str
    freshness: str
    age_ms: "float | None"
    ttl_ms: "float | None"
    observed_at_ms: "float | None"
    basis: str
    rule_id: str
    rule_version: "str | int | None"
    evidence_refs: tuple
    issues: tuple
    withheld: object = None      # core 에 싣지 않은 소스 값(설명용)

    @property
    def usable(self) -> bool:
        return self.status in USABLE


def spec_of(ctx):
    if ctx.spec is not None:
        return ctx.spec
    from .purpose import PURPOSES
    p = PURPOSES.get(ctx.core.purpose)
    if p is None or p.version != ctx.core.purpose_version:
        raise LookupError(f"목적 {ctx.core.purpose}@{ctx.core.purpose_version} 의 명세가 없다 -- "
                          f"투영하려면 from_dict(…, purposes=…) 로 그 명세를 준다")
    return p


def _required(spec) -> dict:
    return {(r.role, r.name): r.required for r in spec.refs}


def view(ctx, key: str) -> StateView:
    cs = ctx._core_state(key)
    pv = ctx.provenance.state(key)
    req = _required(spec_of(ctx)).get((pv.role, pv.name), False)
    now = dict(ctx.core.as_of).get(pv.source)
    age = None if (pv.observed_at_ms is None or now is None) else now - pv.observed_at_ms
    codes = {i.code for i in pv.issues}
    if pv.permanent:
        fresh = PERMANENT
    elif age is None or FUTURE_OBSERVATION in codes:
        fresh = UNTIMED
    elif cs.status == STALE or STALE_AT_SOURCE in codes or any(c.startswith("STALE_") for c in codes) \
            or (pv.ttl_ms is not None and age > pv.ttl_ms):
        fresh = STALE
    else:
        fresh = FRESH
    return StateView(key, pv.role, pv.entity, pv.name, pv.source, req, cs.value, cs.status, pv.source_status, fresh,
                     age, None if pv.permanent else pv.ttl_ms, pv.observed_at_ms, pv.basis, pv.rule_id,
                     pv.rule_version, pv.evidence_refs, pv.issues, pv.withheld)


def views(ctx) -> tuple:
    return tuple(view(ctx, s.key) for s in ctx.core.states)


def validity(ctx) -> Validity:
    vs = views(ctx)
    usable = tuple(v.key for v in vs if v.usable)
    unc = tuple(f"{v.key}={v.status}" for v in vs if v.status in (UNKNOWN, STALE, INVALID))
    na = tuple(v.key for v in vs if v.status == NOT_APPLICABLE)
    missing = tuple(v.key for v in vs if v.required and not v.usable and v.status != NOT_APPLICABLE)
    rejected = tuple(f"{v.key}:{','.join(i.code for i in v.issues)}" for v in vs
                     if v.issues and v.status != v.source_status)
    return Validity(complete=not missing, usable=usable, uncertain=unc, not_applicable=na,
                    missing_required=missing, rejected=rejected)


def actions(ctx) -> tuple:
    spec = spec_of(ctx)
    missing = dict(ctx.provenance.missing)
    avail = set(ctx.core.actions)
    return tuple(Action(a.name, a.name in avail, tuple(a.requires), tuple(missing.get(a.name, ())), a.meaning)
                 for a in spec.actions)
