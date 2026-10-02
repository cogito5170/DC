"""Freeze -- 정준 JSON · 내용 해시 · 기록에서 되살리기.

결정 문맥의 id 는 내용의 sha256 이다. 그래서
    같은 상태 + 같은 목적 + 같은 '지금' + 같은 판본 -> 같은 id   (재현)
    만든 뒤 한 칸이라도 바뀌면 verify() 가 False                  (변조 탐지)
    기록(to_dict)에서 되살릴 때 digest 가 안 맞으면 거절           (from_dict)
"""
from __future__ import annotations

import hashlib
import json

from .model import Action, Constraint, DecisionContext, Provenance, StateView, Validity


class SnapshotError(ValueError):
    pass


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest_of(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def _subject(d: dict) -> tuple:
    return tuple((r, tuple(e) if isinstance(e, list) else e) for r, e in sorted(d.items()))


def freeze(purpose, as_of, subject, states, constraints, capabilities, actions, validity, provenance) -> DecisionContext:
    tmp = DecisionContext("", "", purpose, as_of, subject, tuple(states), tuple(constraints), tuple(capabilities),
                          tuple(actions), validity, provenance)
    dg = digest_of(tmp.body())
    return DecisionContext("dc-" + dg[:16], dg, purpose, as_of, subject, tmp.states, tmp.constraints,
                           tmp.capabilities, tmp.actions, validity, provenance)


def from_dict(d: dict) -> DecisionContext:
    """기록된 문맥을 되살린다. digest 가 안 맞으면 SnapshotError -- 고친 기록으로 결정을 '재현' 하지 못하게."""
    ctx = DecisionContext(
        d["id"], d["digest"], d["purpose"], tuple(sorted(d["as_of"].items())), _subject(d["subject"]),
        tuple(StateView.from_dict(s) for s in d["states"]),
        tuple(Constraint.from_dict(c) for c in d["constraints"]),
        tuple(sorted(d["capabilities"].items())),
        tuple(Action.from_dict(a) for a in d["actions"]),
        Validity.from_dict(d["validity"]), Provenance.from_dict(d["provenance"]))
    if not ctx.verify():
        raise SnapshotError(f"{d.get('id')}: 내용이 digest 와 맞지 않는다")
    return ctx
