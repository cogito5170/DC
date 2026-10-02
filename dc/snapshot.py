"""Freeze -- 정준 JSON · 내용 해시 · 기록에서 되살리기.

결정 문맥의 digest 는 core + provenance 의 sha256 이고 id 는 그 앞 16 자다. 그래서
    같은 상태 + 같은 목적 + 같은 '지금' + 같은 판본 -> 같은 id   (재현)
    만든 뒤 한 칸이라도 바뀌면 verify() 가 False                  (변조 탐지)
    기록(to_dict)에서 되살릴 때 digest 가 안 맞으면 거절           (from_dict)
"""
from __future__ import annotations

import hashlib
import json

from .model import Core, DecisionContext, Provenance


class SnapshotError(ValueError):
    pass


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest_of(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def freeze(core: Core, provenance: Provenance, spec=None) -> DecisionContext:
    dg = digest_of({"core": core.to_dict(), "provenance": provenance.to_dict()})
    return DecisionContext(dg, core, provenance, spec)


def from_dict(d: dict, purposes: "dict | None" = None) -> DecisionContext:
    """기록된 문맥을 되살린다. digest 가 안 맞으면 SnapshotError -- 고친 기록으로 결정을 '재현' 하지 못하게.

    purposes: 투영(필수 여부 · 행동의 뜻)에 쓸 목적 명세 {이름: Purpose}. 이름과 판본이 맞을 때만 붙인다."""
    ctx = DecisionContext(d["digest"], Core.from_dict(d["core"]), Provenance.from_dict(d["provenance"]))
    if not ctx.verify():
        raise SnapshotError(f"{ctx.id}: 내용이 digest 와 맞지 않는다")
    spec = (purposes or {}).get(ctx.core.purpose)
    if spec is not None and spec.version == ctx.core.purpose_version:
        ctx = DecisionContext(ctx.digest, ctx.core, ctx.provenance, spec)
    return ctx
