"""MS 런타임에 꽂기 -- `ms.runtime.Runtime(..., state_reader=MSStateReader(...))`.

MS 의 자리(`state_reader(usage_manager, sid) -> {"state", "record"}`)는 MS 가 DC 를 import 하지 않게 하려고 만든 이음매다.
이 모듈도 MS 를 import 하지 않는다 -- 두 저장소는 함수 꼴 하나로만 맞물린다.

    요청마다:  MS Runtime.handle
                 └─ state_reader(um, sid)            ← 여기
                      └─ DecisionContextBuilder.build(purpose="context_runtime", subject={"session": sid}, now_ms=…)
                      └─ policy_state(ctx, "session")  쓸 수 있는 상태만 값, 나머지 None(= MS 의 '모름 = 고정대로')
                 └─ ContextRuntime.plan(state) · ProviderPolicy.select(state)
                 └─ MS DecisionRecord.state_source = {id, digest, purpose@판본, reuse_key, complete, uncertain}

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

    def __call__(self, usage_manager, sid: str) -> dict:
        extra = self.subject(sid) if callable(self.subject) else (self.subject or {})
        ctx = self.b.build(self.purpose, {**extra, self.role: sid}, now_ms=self._now(),
                           constraints=self.constraints, capabilities=self.capabilities)
        self.last = ctx
        if self.sink is not None:
            self.sink(ctx)
        state = policy_state(ctx, self.role)
        for name, versions in ctx.provenance.sources:
            model = dict(versions).get("model")
            if model:
                state["model_version"] = model
        return {"state": state,
                "record": {"id": ctx.id, "digest": ctx.digest, "purpose": ctx.purpose,
                           "purpose_version": ctx.core.purpose_version, "reuse_key": ctx.reuse_key,
                           "complete": ctx.validity.complete, "uncertain": list(ctx.validity.uncertain)}}
