"""소스 -- State 층을 DC 쪽 꼴(StateRecord)로 읽는 얇은 어댑터. **상태를 계산하지 않는다.**

소스가 지켜야 할 것(StateSource):
    name            소스 이름(목적 명세의 StateRef.source 와 맞춘다)
    authoritative   True 여야 등록된다. LLM 제안 · 사람 의견 같은 권위 없는 것은 소스가 될 수 없다
    read(entity, name, now_ms) -> StateRecord   없으면 None 이 아니라 UNKNOWN 레코드를 준다
    domain(entity, name) -> tuple | None         값 집합(UNKNOWN · NOT_APPLICABLE 은 빼고). 모르면 None
    versions() -> dict                           규칙 · 설정 · 모형 판본 -- 문맥의 provenance 에 남는다

어댑터 둘은 Sensor · MS 를 **import 하지 않는다**(덕 타이핑). 저장소가 따로라 서로의 설치를 요구하지 않기 위해서다.

    SensorSource   llmsensor 의 내보내기 계약(llmsensor.state-export/2) -- 실행 단위(agent · task · runtime · tool) 의미 상태
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
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OBSERVED")
        return r

    def domain(self, entity, name):
        return self._domains.get(name)

    def versions(self) -> dict:
        return dict(self._versions)


SENSOR_CONTRACT = "llmsensor.state-export/2"     # /1 은 거절한다(유일한 소비자가 DC 라 판본을 함께 올렸다, CMD-D7)


class SensorSource:
    """Sensor 의 **내보내기 계약**(llmsensor.state-export/2)만 읽는다 -- 엔진 안(current · view · reg · cfg)은 보지 않는다.

    계약: engine.EXPORT_CONTRACT · state_catalog() · export_state(entity, name, now) · subjects(run_id) · as_of(run_id).
    계약 판본이 다르면 추측하지 않고 거절한다(SourceError). 판정기 · 참조 정책 · Sensor 자체의 결정 문맥은 읽지 않는다.

    시각 기준은 그 실행의 관측 시각(unix_ms 또는 monotonic_ms). run_id 를 주면 now_ms() 가 그 실행에서 본 가장 늦은 관측 시각을
    준다 -- '마지막 사건 기준의 지금' 이라 그 뒤의 낡음은 안 보인다. 실제 낡음을 보려면 같은 시각 기준의 '지금' 을 빌더에 직접 준다.
    """
    authoritative = True

    def __init__(self, engine, name: str = "sensor", run_id: "str | None" = None):
        got = getattr(engine, "EXPORT_CONTRACT", None)
        if got != SENSOR_CONTRACT:
            raise SourceError(f"Sensor 내보내기 계약이 {got!r} -- 이 어댑터는 {SENSOR_CONTRACT!r} 만 읽는다")
        self.engine, self.name, self.run_id = engine, name, run_id
        self._catalog = engine.state_catalog()

    def read(self, entity, name, now_ms):
        d = self.engine.export_state(entity, name, now_ms)
        return StateRecord(self.name, d["entity"], d["name"], d["value"], d["status"], d["basis"] or "OBSERVED",
                           rule_id=d["rule_id"] or "", rule_version=d["rule_version"],
                           evidence_refs=tuple(d["evidence_refs"]), observed_at_ms=d["observed_at"],
                           ttl_ms=d["ttl_ms"], permanent=bool(d["final"]), since_ms=d["since"],
                           time_base=d["time_base"], local=self._local(d))

    @staticmethod
    def _local(d):
        """계약의 entity_ref 로 지역 이름을 -- 행동 id 처럼 `:` 를 품어도 맞다(CMD-D16). Sensor 가 못 가른 실체(scope null)는
        id 전체를 지역 이름으로 써서 다른 실체와 섞이지 않게 한다."""
        ref = d.get("entity_ref") or {}
        if ref.get("local") is not None:
            return ref["local"]
        return d["entity"] if ref.get("scope") is None else None

    def domain(self, entity, name):
        st = self._catalog["states"].get(name)
        return tuple(st["values"]) if st else None

    def versions(self) -> dict:
        c = self._catalog
        return {"contract": c["contract"], "config": c["config_version"],
                "rules": ",".join(f"{v['rule_id']}@{v['rule_version']}" for _, v in sorted(c["states"].items()))}

    def subject(self, run_id: "str | None" = None) -> dict:
        s = dict(self.engine.subjects(run_id or self.run_id))
        s.pop("scope", None)                                  # 역할이 아니다 -- 실체 id 의 범위(BD-32)
        return {k: tuple(v) if isinstance(v, list) else v for k, v in s.items()}   # 여러 실체 역할(tool · action) -> 고정 tuple

    def now_ms(self) -> float:
        if self.run_id is None:
            raise ValueError("SensorSource 에 run_id 가 없다 -- '지금' 을 빌더에 직접 주거나 run_id 를 준다")
        at = self.engine.as_of(self.run_id)["at"]
        if at is None:
            raise ValueError(f"실행 {self.run_id} 의 관측 시각이 없다 -- '지금' 을 지어내지 않는다")
        return float(at)


def _base_prop(name: str) -> str:
    """MS 의 `x__n`(창 안 표본 수) · `x__sum`(창 안 합)은 측정 속성 x 에서 나온다 -- 근거는 x 의 측정 창이다."""
    for suf in ("__n", "__sum"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


class MSUsageSource:
    """ms.manager.StateManager 의 **파생 상태**만 읽는다(측정 창 `measurements` 의 원 측정은 값으로 내지 않는다 -- id 만 근거로).

    MS 의 시각은 초(clock) 다 -- ms 로 바꿔 준다. 파생의 시각은 MS 규약대로 **관측** 입력 중 가장 오래된 것이다(설정은 빠진다, MS PC-03).
    MS 의 문턱은 손으로 둔 것이라(usage_model: "문턱은 잰 것이 아니다") 근거 종류는 OPERATOR_ASSUMED 다.
    """
    authoritative = True

    def __init__(self, manager, model_version: str, name: str = "ms", time_base: "str | None" = None):
        """time_base: MS 시계의 기준(BD-33). MS 는 시계를 주입받으므로(PC-12) 기준을 모르면 None 으로 둔다 -- 짐작하지 않는다."""
        self.manager, self.model_version, self.name, self.time_base = manager, model_version, name, time_base

    def now_ms(self) -> float:
        return self.manager.clock() * 1000.0

    def _model(self, entity):
        node = self.manager.graph.nodes.get(entity)
        return node, (self.manager.models.get(node.model) if node is not None else None)

    def read(self, entity, name, now_ms):
        node, model = self._model(entity)
        if node is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OPERATOR_ASSUMED")
        d = model.derived.get(name)
        rule_id = f"{node.model}.{name}"
        if d is None:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OPERATOR_ASSUMED")
        ttl = model.ttl_of(name)
        ttl_ms = None if ttl is None else ttl * 1000.0
        v = node.props.get(name)
        if v is None or not v.derived:
            return StateRecord(self.name, entity, name, None, UNKNOWN, "OPERATOR_ASSUMED", rule_id=rule_id,
                               rule_version=self.model_version, ttl_ms=ttl_ms)
        inputs = sorted(d.inputs)
        refs = []
        for p in dict.fromkeys(_base_prop(x) for x in inputs):
            win = self.manager.measurements.get(entity, {}).get(p)     # 측정 창(MS PC-04 의 새 이름). 설정 입력은 근거가 아니다
            if win:
                refs.extend(x.src for x in win)
            elif node.props.get(p) is not None:
                refs.append(node.props[p].src)
        return StateRecord(self.name, entity, name, v.value, INFERRED, "OPERATOR_ASSUMED", rule_id=rule_id,
                           rule_version=self.model_version, evidence_refs=tuple(refs), observed_at_ms=v.ts * 1000.0,
                           ttl_ms=ttl_ms, time_base=self.time_base)

    def domain(self, entity, name):
        node, model = self._model(entity)
        d = model.derived.get(name) if model else None
        if d is None:
            return None
        vals = [c["value"] for c in d.cases] + ([d.default] if d.default is not None else [])
        return tuple(dict.fromkeys(vals))

    def versions(self) -> dict:
        return {"model": self.model_version}


class MSGraphSource:
    """MS 의 세계 그래프에 **질의**한다(baseline BD-26 · PC-23) -- 질의를 돌리는 것은 MS 의 `run_query` 다(DC 는 복제하지 않는다).

    DC 는 MS 를 import 하지 않으므로 배선이 둘을 준다:
        MSGraphSource(manager, run_query=ms.query.run_query, make_query=ms.query.StateQuery.from_dict)
    결과 행의 속성마다: MS 가 낡았다고 하면 STALE, 파생 값이면 INFERRED, 아니면 OBSERVED. 근거는 MS 의 값 출처(텔레메트리 id 또는
    `derived:…`)를 참조로만 남긴다. 상태(read)는 내지 않는다 -- 이 소스는 질의만 한다.
    """
    authoritative = True

    def __init__(self, manager, run_query, make_query, name: str = "ms_world"):
        self.manager, self.run_query, self.make_query, self.name = manager, run_query, make_query, name

    def now_ms(self) -> float:
        return self.manager.clock() * 1000.0

    def read(self, entity, name, now_ms):
        return StateRecord(self.name, entity, name, None, UNKNOWN, "OBSERVED")

    def domain(self, entity, name):
        return None

    def versions(self) -> dict:
        return {"graph": "ms-state-graph", "models": ",".join(sorted(self.manager.models))}

    def query(self, spec: dict, now_ms) -> dict:
        res = self.run_query(self.make_query(spec), self.manager)
        rows = []
        for r in res.rows:
            node = self.manager.graph.nodes.get(r.id)
            props = {}
            for p, v in r.props.items():
                val = node.props.get(p) if node is not None else None
                st = "STALE" if v["stale"] else ("INFERRED" if (val is not None and val.derived) else "OBSERVED")
                props[p] = {"value": v["value"], "status": st, "ref": val.src if val is not None else None,
                            "observed_at_ms": val.ts * 1000.0 if val is not None else None}
            rows.append({"id": r.id, "model": r.model, "props": props, "must": r.must,
                         "edges": [tuple(e) for e in r.edges]})
        return {"rows": rows, "matched": res.matched}
