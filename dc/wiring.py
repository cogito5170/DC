"""MS 런타임에 꽂기 -- `ms.runtime.Runtime(..., state_reader=MSStateReader(...))`.

MS 의 자리(`state_reader(usage_manager, sid[, request]) -> {"state", "record"[, "queries"]}`)는 MS 가 DC 를 import 하지 않게 하려고
만든 이음매다. 이 모듈도 MS 를 import 하지 않는다 -- 두 저장소는 함수 꼴 하나로만 맞물린다.

    요청마다:  MS Runtime.handle
                 └─ state_reader(um, sid, request)   ← 여기
                      └─ DecisionContextBuilder.build(purpose="context_runtime", subject={"session": sid}, now_ms=…,
                                                      queries=요청의 queries)      (PC-23 · CMD-D13)
                      └─ policy_state(ctx, "session")  쓸 수 있는 상태만 값, 나머지 None(= MS 의 '모름 = 고정대로')
                      └─ queries = {이름: core 질의}   CR 은 그래프에 직접 묻지 않고 이것으로 맥락을 짓는다
                 └─ ContextRuntime.plan(state) · ProviderPolicy.select(state)
                 └─ MS DecisionRecord.state_source = {id, digest, purpose@판본, reuse_key, complete, uncertain, default_action, role[, queries]}

요청 질의는 **목적이 요청 질의를 받는 소스(`query_sources`)가 빌더에 다 꽂혀 있을 때만** 넘긴다. 아니면(세계 그래프 소스 없이 사용 상태만
꽂은 리더) 질의를 돌리지 않고 `queries` 를 돌려주지 않는다 -- MS 가 예전처럼 스스로 그래프에 묻는다. 질의의 `allow_stale`(BD-65)은
그대로 넘어가, 그 질의에서만 낡은 값이 STALE 표시와 함께 core 에 실린다.

`last` 에 마지막 결정 문맥이 남는다(실행 기록의 digest 와 맞춰 볼 수 있다). `sink` 를 주면 문맥마다 불린다(감사 기록용).
"""
from __future__ import annotations

from .bridge import policy_state


class MSStateReader:
    def __init__(self, builder, purpose: str = "context_runtime", role: str = "session", *, now_ms=None,
                 subject=None, constraints=(), capabilities: "dict | None" = None, sink=None):
        """now_ms: None 이면 '지금' 을 줄 수 있는 소스(now_ms() 가 있는 것)에게 묻는다. 수 · {소스: 수} · 호출 가능도 된다.
        subject: 세션 말고 더 붙일 역할(예: Sensor 의 agent · runtime). dict 또는 sid -> dict."""
        self.b, self.purpose, self.role = builder, purpose, role
        self.now_ms, self.subject, self.sink = now_ms, subject, sink
        self.constraints, self.capabilities = tuple(constraints), dict(capabilities or {})
        self.last = None

    def _now(self):
        if self.now_ms is not None:
            return self.now_ms() if callable(self.now_ms) else self.now_ms
        out, lack = {}, []
        for name, src in self.b.sources.items():
            (out.__setitem__(name, src.now_ms()) if hasattr(src, "now_ms") else lack.append(name))
        if lack:
            raise ValueError(f"소스 {lack} 는 '지금' 을 모른다 -- now_ms 를 주어라(시각 기준이 소스마다 다를 수 있다)")
        return out

    def _queries(self, request) -> "list | None":
        """요청의 질의 -> 빌더에 줄 꼴. 돌릴 수 없으면(요청 없음 · 목적이 요청 질의를 안 받음 · 그 소스가 없음) None."""
        if request is None:
            return None
        P = self.b._purpose(self.purpose)
        if not P.query_sources or any(s not in self.b.sources for s in P.query_sources):
            return None
        out = []
        for q in request.get("queries", ()) or ():
            if not isinstance(q, dict):                 # 질의 객체(예: MS StateQuery 데이터클래스)도 받는다
                import dataclasses
                q = dataclasses.asdict(q)
            out.append(dict(q))
        return out

    def __call__(self, usage_manager, sid: str, request: "dict | None" = None) -> dict:
        extra = self.subject(sid) if callable(self.subject) else (self.subject or {})
        qs = self._queries(request)
        ctx = self.b.build(self.purpose, {**extra, self.role: sid}, now_ms=self._now(),
                           constraints=self.constraints, capabilities=self.capabilities, queries=qs or ())
        self.last = ctx
        if self.sink is not None:
            self.sink(ctx)
        state = policy_state(ctx, self.role)
        for name, versions in ctx.provenance.sources:
            model = dict(versions).get("model")
            if model:
                state["model_version"] = model
        out = {"state": state,
               "record": {"id": ctx.id, "digest": ctx.digest, "purpose": ctx.purpose,
                          "purpose_version": ctx.core.purpose_version, "reuse_key": ctx.reuse_key,
                          "complete": ctx.validity.complete, "uncertain": list(ctx.validity.uncertain),
                          "default_action": ctx.default_action,       # 목적의 안전 기본 결정(BD-76 · BD-81) -- MS 가 읽는다
                          "role": self.role}}      # state 의 이름이 어느 역할의 상태인가 -- 문맥 키는 "<role>.<이름>" (BD-100)
        if qs is not None:
            out["queries"] = {q.name: q.to_dict() for q in ctx.core.queries}
        return out
