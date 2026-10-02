"""결정 문맥 보관 -- 원장 · 재현 · 감사가 문맥 id 로 다시 꺼낸다(Sensor decision/context 의 ContextStore 에서 옮겨 옴, BD-05).

    put(ctx) -> id      같은 id 는 한 번만(내용 주소라 같은 id = 같은 내용). digest 가 안 맞는 문맥은 받지 않는다
    get(id)             고정된 그 문맥 -- 뒤에 State 가 바뀌어도 그대로다
    dump() / load(rows) 기록 꼴(to_dict) 로 내보내고 들여온다. 들여올 때 digest 를 검사한다(from_dict)

근거 사슬을 문맥마다 복사하지 않는다(Sensor 판은 문맥마다 6–22 KB 를 떴다, BD-05 의 단점). 근거는 `evidence_refs` 참조로 남고,
사슬은 소스(예: Sensor `explain`)에서 펼친다 -- Evidence 는 참조다(BD-06).
"""
from __future__ import annotations

from .model import DecisionContext
from .snapshot import SnapshotError, from_dict


class ContextStore:
    def __init__(self):
        self._d: dict = {}

    def put(self, ctx: DecisionContext) -> str:
        if not ctx.verify():
            raise SnapshotError(f"{ctx.id}: 내용이 digest 와 맞지 않는다 -- 보관하지 않는다")
        self._d.setdefault(ctx.id, ctx)
        return ctx.id

    def get(self, context_id: str) -> DecisionContext:
        return self._d[context_id]

    def __contains__(self, context_id) -> bool:
        return context_id in self._d

    def __len__(self) -> int:
        return len(self._d)

    def dump(self) -> list:
        return [self._d[k].to_dict() for k in sorted(self._d)]

    def load(self, rows) -> "ContextStore":
        for d in rows:
            self.put(from_dict(d))
        return self
