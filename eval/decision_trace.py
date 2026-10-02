"""실행별 결정 흐름 -- 정책 쓸모 평가(policy_impact.py)와 같은 재생을 하되, 목적 하나의 평가점마다 결정과 그 결정이 읽은 상태를 남긴다.

    python3 eval/decision_trace.py --out trace.json [--purpose execution_control]
    python3 eval/decision_trace.py --diff a.json b.json          # 두 흐름에서 결정이 갈린 실행 · 평가점

두 Sensor 판(DC_SENSOR_PATH)으로 같은 레코드를 돌려 --diff 하면, 어떤 상태 규칙 변경이 어느 실행의 어느 결정을 바꿨는지 짚을 수 있다
(baseline CMD-D11). 내용(질문 · 답 · 명령)은 없다 -- 실행 id · 평가점 번호 · 상태 값 · 유효성 · 근거 시각 · 행동 · 규칙 까닭뿐.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _policy_impact():
    spec = importlib.util.spec_from_file_location("policy_impact", ROOT / "eval" / "policy_impact.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def trace(purpose: str) -> dict:
    P = _policy_impact()
    from llmsensor.sensing.quality import external_label_batch
    from llmsensor.state import StateEngine, from_telemetry
    from dc import DecisionContextBuilder, SensorSource
    d = json.loads(P.LABELS.read_text())
    resolved, nolog = set(d["resolved"]), set(d.get("no_logs", []))
    pol = dict(P.POLICIES)[purpose]
    byrun = collections.defaultdict(list)
    for b in from_telemetry(P.load()):
        byrun[b.run_id].append(b)
    E = StateEngine()
    src = SensorSource(E)
    B = DecisionContextBuilder([src])
    out = {}
    for run in sorted(byrun):
        extra = []
        if run.startswith("sweagent:"):
            iid = run.split(":", 1)[1]
            if iid not in nolog:
                extra.append(external_label_batch(run, iid in resolved, "swe-bench-lite hidden tests", iid))
        steps = []
        for i, b in enumerate(byrun[run] + extra):
            E.ingest(b)
            now = E.as_of(run)["at"] or 0.0
            c = B.build(purpose, src.subject(run), now_ms=now, capabilities=P.CAP)
            dec = pol.decide(c)
            states = {}
            for s in c.core.states:
                pv = c.provenance.state(s.key)
                if s.key.startswith("tool["):
                    continue
                states[s.key] = [s.value, s.status, pv.observed_at_ms, pv.withheld, pv.rule_id]
            steps.append({"i": i, "now": now, "action": dec.action, "why": dec.reason, "used": list(dec.used),
                          "states": states})
        out[run] = steps
    return {"purpose": purpose, "runs": out}


def diff(a: dict, b: dict) -> list:
    found = []
    for run in sorted(set(a["runs"]) & set(b["runs"])):
        sa, sb = a["runs"][run], b["runs"][run]
        ca = sum(1 for x, y in zip(sa, sa[1:]) if x["action"] != y["action"])
        cb = sum(1 for x, y in zip(sb, sb[1:]) if x["action"] != y["action"])
        if ca == cb and [x["action"] for x in sa] == [x["action"] for x in sb]:
            continue
        first = next((k for k, (x, y) in enumerate(zip(sa, sb)) if x["action"] != y["action"]), None)
        found.append({"run": run, "decision_changes": [ca, cb], "first_divergent_step": first,
                      "a": sa[first] if first is not None else None, "b": sb[first] if first is not None else None,
                      "b_prev": sb[first - 1] if first else None})
    return found


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--purpose", default="execution_control")
    ap.add_argument("--diff", nargs=2)
    a = ap.parse_args(argv)
    if a.diff:
        r = diff(*(json.loads(Path(p).read_text()) for p in a.diff))
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    t = trace(a.purpose)
    Path(a.out).write_text(json.dumps(t, ensure_ascii=False))
    print(f"{len(t['runs'])} 실행 · {sum(len(v) for v in t['runs'].values())} 평가점 -> {a.out}")


if __name__ == "__main__":
    main()
