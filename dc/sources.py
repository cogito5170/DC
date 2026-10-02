"""소스 -- State 층을 DC 쪽 꼴(StateRecord)로 읽는 얇은 어댑터. **상태를 계산하지 않는다.**

소스가 지켜야 할 것(StateSource):
    name            소스 이름(목적 명세의 StateRef.source 와 맞춘다)
    authoritative   True 여야 등록된다. LLM 제안 · 사람 의견 같은 권위 없는 것은 소스가 될 수 없다
    read(entity, name, now_ms) -> StateRecord   없으면 None 이 아니라 UNKNOWN 레코드를 준다
    domain(entity, name) -> tuple | None         값 집합(UNKNOWN · NOT_APPLICABLE 은 빼고). 모르면 None
    versions() -> dict                           규칙 · 설정 · 모형 판본 -- 문맥의 provenance 에 남는다

어댑터 둘은 Sensor · MS 를 **import 하지 않는다**(덕 타이핑). 저장소가 따로라 서로의 설치를 요구하지 않기 위해서다.

    SensorSource   llmsensor.state.StateEngine  -- 실행 단위(agent · task · runtime · tool) 의미 상태
    MSUsageSource  ms.manager.StateManager + ms.usage_model -- 세션 단위 사용 상태
    StaticSource   (실체, 이름) -> StateRecord 표. 시험 · 기록 재생용
"""
from __future__ import annotations

from .model import INFERRED, UNKNOWN, StateRecord


class SourceError(ValueError):
    pass


def check_source(src) -> None:
    for attr in ("name", "read", "domain", "versions"):
        if not hasattr(src, attr):
            raise SourceError(f"소스에 {attr} 가 없다")
    if getattr(src, "authoritative", False) is not True:
        raise SourceError(f"소스 {src.name}: 권위 없는 소스(제안 · 의견)는 결정 문맥의 상태가 될 수 없다")


class StaticSource:
    """표로 주는 소스. 기록된 레코드로 문맥을 다시 짓거나 시험에 쓴다."""
    authoritative = True

    def __init__(self, name: str, records=(), domains: "dict | None" = None, versions: "dict | None" = None):
        self.name = name
        self.records = {(r.entity, r.name): r for r in records}
        self._domains = dict(domains or {})
        self._versions = dict(versions or {"static": "1"})

    def put(self, rec: StateRecord) -> None:
        self.records[(rec.entity, rec.name)] = rec

    def read(self, entity, name, now_ms):
        r = self.records.get((entity, name))
        if r is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OBSERVED", reason="표에 없다")
        return r

    def domain(self, entity, name):
        return self._domains.get(name)

    def versions(self) -> dict:
        return dict(self._versions)


class SensorSource:
    """llmsensor.state.StateEngine 을 읽는다. 시각 기준은 그 엔진의 관측 시각(unix_ms 또는 monotonic_ms)."""
    authoritative = True

    def __init__(self, engine, name: str = "sensor"):
        self.engine, self.name = engine, name

    def read(self, entity, name, now_ms):
        E = self.engine
        rule = E.reg.rules.get(name)
        st = E.current.get((entity, name))
        if st is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, rule.basis.value if rule else "OBSERVED",
                               rule_id=rule.id if rule else "", rule_version=rule.version if rule else None,
                               reason="아직 계산되지 않았다" if rule else "Sensor 에 없는 상태",
                               ttl_ms=E.cfg.ttl_ms.get(name))
        sv = E.view(st, now_ms)
        return StateRecord(self.name, entity, name, st.value, sv.status.value, st.basis.value, rule_id=st.rule_id,
                           rule_version=st.rule_version, evidence_refs=tuple(sv.evidence_refs), reason=st.reason,
                           observed_at_ms=st.observed_at, ttl_ms=E.cfg.ttl_ms.get(name), permanent=bool(st.final),
                           since_ms=st.since)

    def domain(self, entity, name):
        r = self.engine.reg.rules.get(name)
        return tuple(r.values) if r else None

    def versions(self) -> dict:
        E = self.engine
        return {"config": E.cfg.version,
                "rules": ",".join(f"{r.id}@{r.version}" for _, r in sorted(E.reg.rules.items()))}

    def subject(self, run_id: str) -> dict:
        """실행 하나의 역할 -> 실체. 도구는 그 실행에서 상태가 있는 것 전부."""
        tools = tuple(sorted({e for (e, _n) in self.engine.current if e.startswith(f"tool:{run_id}:")}))
        return {"agent": f"agent:{run_id}", "task": f"task:{run_id}", "runtime": f"runtime:{run_id}", "tool": tools}


class MSUsageSource:
    """ms.manager.StateManager 의 **파생 상태**만 읽는다(evidence 창의 원 측정은 값으로 내지 않는다 -- id 만 근거로).

    MS 의 시각은 초(clock) 다 -- ms 로 바꿔 준다. 파생의 시각은 MS 규약대로 입력 중 가장 오래된 관측이다.
    MS 의 문턱은 손으로 둔 것이라(usage_model: "문턱은 잰 것이 아니다") 근거 종류는 OPERATOR_ASSUMED 다.
    """
    authoritative = True

    def __init__(self, manager, model_version: str, name: str = "ms"):
        self.manager, self.model_version, self.name = manager, model_version, name

    def now_ms(self) -> float:
        return self.manager.clock() * 1000.0

    def _model(self, entity):
        node = self.manager.graph.nodes.get(entity)
        return node, (self.manager.models.get(node.model) if node is not None else None)

    def read(self, entity, name, now_ms):
        node, model = self._model(entity)
        if node is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OPERATOR_ASSUMED", reason="MS 에 없는 실체")
        d = model.derived.get(name)
        rule_id = f"{node.model}.{name}"
        if d is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OPERATOR_ASSUMED",
                               reason=f"모형 {node.model} 에 없는 파생 상태")
        ttl = model.ttl_of(name)
        ttl_ms = None if ttl is None else ttl * 1000.0
        v = node.props.get(name)
        if v is None or not v.derived:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OPERATOR_ASSUMED", rule_id=rule_id,
                               rule_version=self.model_version, ttl_ms=ttl_ms,
                               reason="입력이 모자라 모름(모형은 기본값으로 메우지 않는다)")
        inputs = sorted(d.inputs)
        refs = []
        for p in inputs:
            win = self.manager.evidence.get(entity, {}).get(p)
            if win:
                refs.extend(x.src for x in win)
            elif node.props.get(p) is not None:
                refs.append(node.props[p].src)
        return StateRecord(self.name, entity, name, v.value, INFERRED, "OPERATOR_ASSUMED", rule_id=rule_id,
                           rule_version=self.model_version, evidence_refs=tuple(refs),
                           reason=f"{self.model_version}: 입력 {', '.join(inputs)}", observed_at_ms=v.ts * 1000.0,
                           ttl_ms=ttl_ms)

    def domain(self, entity, name):
        node, model = self._model(entity)
        d = model.derived.get(name) if model else None
        if d is None:
            return None
        vals = [c["value"] for c in d.cases] + ([d.default] if d.default is not None else [])
        return tuple(dict.fromkeys(vals))

    def versions(self) -> dict:
        return {"model": self.model_version}
