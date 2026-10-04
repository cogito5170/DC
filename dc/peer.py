"""이웃 상태 소스와 peer_interaction 목적의 refs 짓기(baseline POL-3 BD-309 · NETWORK.md 2.3).

이웃의 state-export/2 파일은 **메시지가 아니라 소스**다: 값은 그대로(바꾸지 않는다) · 이웃의 basis 도 그대로 StateRecord 가 되어
`peer[<j>].<ref>` 키로 빌더에 들어가고, 다른 소스와 똑같이 Validate(근거 · 권위 · 신선도)를 지난다. 이 모듈은 값을 걸러 주지 않는다.

파일 꼴(JSON): {"contract": "llmsensor.state-export/2", "node": "<j>", "states": [export_state 사전, …]}
    export_state 사전 = Sensor export_state(entity, name, now) 가 내는 칸(entity · name · value · status · basis · rule_id ·
    rule_version · evidence_refs · observed_at · ttl_ms · final · since · time_base). ref 이름은 "<역할>.<상태>"(역할 = entity 의 `:` 앞).
"""
from __future__ import annotations

import json

from .model import UNKNOWN, StateRecord
from .purpose import PEER_INTERACTION, PurposeError, StateRef
from .sources import SENSOR_CONTRACT, SourceError

PEER_SOURCE, EDGE_SOURCE = "peer", "net"       # 소스 이름 -- peer = 이웃 export, net = 간선 가중치 pi[j]
PEER_ROLE, EDGE_ROLE = "peer", "pi"


def _ref_of(d: dict) -> str:
    return f"{str(d['entity']).split(':', 1)[0]}.{d['name']}"


class PeerSource:
    """이웃들의 state-export/2 를 `peer[<j>].<ref>` 로 읽는다. 값 · basis 는 이웃이 낸 그대로.

    entity 는 `peer:<j>` (목적의 subject 에서 peer 역할을 여럿으로 준다). 이웃 파일에 그 ref 가 없으면 UNKNOWN.
    """
    authoritative = True

    def __init__(self, exports: "dict | None" = None, name: str = PEER_SOURCE):
        self.name = name
        self.nodes: dict = {}                      # j -> {ref: export_state 사전}
        self._contracts: dict = {}
        for j, doc in (exports or {}).items():
            self.add(doc, node=j)

    @classmethod
    def from_files(cls, paths, name: str = PEER_SOURCE) -> "PeerSource":
        src = cls(name=name)
        for p in paths:
            with open(p, encoding="utf-8") as f:
                src.add(json.load(f))
        return src

    def add(self, doc: dict, node: "str | None" = None) -> str:
        if doc.get("contract") != SENSOR_CONTRACT:
            raise SourceError(f"이웃 export 계약이 {doc.get('contract')!r} -- {SENSOR_CONTRACT!r} 만 읽는다")
        j = node if node is not None else doc.get("node")
        if not isinstance(j, str) or not j:
            raise SourceError("이웃 export 에 node 가 없다")
        self.nodes[j] = {_ref_of(d): d for d in doc.get("states", ())}
        return j

    @staticmethod
    def entity(j: str) -> str:
        return f"{PEER_ROLE}:{j}"

    def read(self, entity, name, now_ms):
        j = entity.split(":", 1)[-1]
        d = self.nodes.get(j, {}).get(name)
        if d is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OBSERVED", local=j)
        return StateRecord(self.name, entity, name, d.get("value"), d.get("status", UNKNOWN), d.get("basis") or "OBSERVED",
                           rule_id=d.get("rule_id") or "", rule_version=d.get("rule_version"),
                           evidence_refs=tuple(d.get("evidence_refs") or ()), observed_at_ms=d.get("observed_at"),
                           ttl_ms=d.get("ttl_ms"), permanent=bool(d.get("final")), since_ms=d.get("since"),
                           time_base=d.get("time_base"), local=j)

    def domain(self, entity, name):
        return None

    def versions(self) -> dict:
        return {"contract": SENSOR_CONTRACT, "peers": ",".join(sorted(self.nodes))}

    def covers(self, j: str, ref: str) -> bool:
        """이웃 j 의 export 가 ref 에 값을 **들고 있나**(UNKNOWN · NOT_APPLICABLE · 값 없음은 덮지 않는다). 쓸 수 있는지는 Validate 가 정한다."""
        d = self.nodes.get(j, {}).get(ref)
        return d is not None and d.get("value") is not None and d.get("status") not in (UNKNOWN, "NOT_APPLICABLE")


def _split(ref: str) -> "tuple[str, str]":
    role, _, name = ref.partition(".")
    if not role or not name:
        raise PurposeError(f"ref {ref!r} 는 '<역할>.<상태>' 꼴이어야 한다")
    return role, name


def peer_interaction_purpose(missing_required=(), uncertain=(), *, own_source: str = "sensor"):
    """PURPOSES['peer_interaction'] 틀에 refs 를 채운 목적.

    필수: 자기 missing_required 와 불확실한 ref(원래의 키 "<역할>.<상태>").
    선택: 그 ref 들의 이웃 값 `peer[<j>].<ref>` 와 간선 가중치 `pi[<j>]`.
    빌드할 때 subject 에 peer = ("peer:<j>", …) · pi = ("pi:<j>", …) 와 자기 역할들을 준다.
    """
    own = tuple(dict.fromkeys(list(missing_required) + list(uncertain)))
    refs = [StateRef(own_source, *_split(r)) for r in own]
    refs += [StateRef(PEER_SOURCE, PEER_ROLE, r, required=False) for r in own]
    refs.append(StateRef(EDGE_SOURCE, EDGE_ROLE, "", required=False))
    return PEER_INTERACTION.with_(refs=tuple(refs))


def peer_capabilities(peers: PeerSource, missing_required=(), *, peer_link: bool = False, base: "dict | None" = None) -> dict:
    """consult 의 문: 이웃 연결이 있고 어떤 이웃 export 가 모자란 ref 하나라도 덮을 때만(peer_covers_missing)."""
    covers = any(peers.covers(j, r) for j in peers.nodes for r in missing_required)
    return {**(base or {}), "peer_link": bool(peer_link), "peer_covers_missing": covers}
