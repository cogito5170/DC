"""정책 쓸모 -- 결정 문맥이 결정을 실제로 바꾸나. (baseline PC-08: Sensor eval/ms_end_to_end.py 의 '정책 쓸모' 부분을 옮겨 옴)

    python3 eval/policy_impact.py [--out eval/results/policy_impact.json]

Sensor 의 실제 레코드(eval/results/sensor_layer_records.jsonl.gz, 301 실행)를 한 묶음씩 다시 흘리며, 묶음마다
Sensor 내보내기 계약 -> dc.SensorSource -> DecisionContext -> refpolicy(시험 정책) 를 돌린다.

재는 것:
    결정론            전체를 두 번 돌려 (문맥 id, 결정) 의 해시가 같은가
    상태 -> 결정      상태가 바뀐 평가점 -> 문맥이 바뀌었나 -> 결정이 바뀌었나
                      state_impact_rate = 결정도 바뀐 / 상태 바뀜 · used_impact_rate = 정책이 그 상태를 썼을 때만 ·
                      alone_impact_rate = 그 상태 혼자 바뀐 점에서만(실행 끝의 동시 변화를 가른다)

가정(결과에 적는다): 능력 {alternate_provider, retry_budget, human_reviewer} = 참. 외부 라벨 = SWE-bench Lite 판정(Sensor eval/data).
시험 정책은 MS 정책이 아니다 -- 쓸모는 '이 시험 정책에 대한' 쓸모다. Sensor 판(reference-*-v1)과 행동 어휘가 달라
(COMPACT_CONTEXT 없음 · 끝난 실행의 행동을 문맥이 거르지 않음) 수가 1:1 로 맞지 않는다.

Sensor 쪽에 남은 것: 팩 · 상태별 계산가능률 · UNKNOWN/STALE 률 · 근거 사슬 덮임(Sensor eval/ms_end_to_end.py).
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSOR = Path(os.environ.get("DC_SENSOR_PATH", ROOT.parent / "Sensor"))
sys.path[0:0] = [str(ROOT), str(SENSOR)]

from dc import BUILDER_VERSION, DecisionContextBuilder, PURPOSES, SensorSource  # noqa: E402
from refpolicy import context as cpol, execution as epol, provider as ppol  # noqa: E402

REC = SENSOR / "eval" / "results" / "sensor_layer_records.jsonl.gz"
LABELS = SENSOR / "eval" / "data" / "swe_lite_20240620_sweagent_claude35sonnet_results.json"
CAP = {"alternate_provider": True, "retry_budget": True, "human_reviewer": True}
POLICIES = (("context_policy", cpol), ("provider_selection", ppol), ("execution_control", epol))


def load():
    with gzip.open(REC, "rt", encoding="utf-8") as f:
        recs = [json.loads(x) for x in f]
    return [r for r in recs if not r["run_id"].startswith("cc_jsonl_child:")]


def replay(recs):
    from llmsensor.sensing.quality import external_label_batch
    from llmsensor.state import StateEngine, from_telemetry
    d = json.loads(LABELS.read_text())
    resolved, nolog = set(d["resolved"]), set(d.get("no_logs", []))
    byrun = collections.defaultdict(list)
    for b in from_telemetry(recs):
        byrun[b.run_id].append(b)
    E = StateEngine()
    src = SensorSource(E)
    B = DecisionContextBuilder([src])
    seqs = collections.defaultdict(list)
    digest = hashlib.sha256()
    for run in sorted(byrun):
        extra = []
        if run.startswith("sweagent:"):
            iid = run.split(":", 1)[1]
            if iid not in nolog:
                extra.append(external_label_batch(run, iid in resolved, "swe-bench-lite hidden tests", iid))
        for b in byrun[run] + extra:
            E.ingest(b)
            now = E.as_of(run)["at"]
            now = 0.0 if now is None else now           # 시각 없는 원천(SWE-agent): 나이를 못 재고 UNTIMED 로 남는다
            subj = src.subject(run)
            for purpose, pol in POLICIES:
                c = B.build(purpose, subj, now_ms=now, capabilities=CAP)
                dec = pol.decide(c)
                views = tuple((s.key, s.value, s.status, s.usable) for s in c.states if s.source == "sensor")
                material = (views, c.available_actions, c.validity.complete)    # 나이 · as_of 를 뺀 결정 관련 내용
                seqs[(run, purpose)].append((views, material, dec.action, dec.used))
                digest.update(f"{run}|{purpose}|{c.id}|{dec.action}".encode())
    return E, seqs, digest.hexdigest()


def impact(seqs):
    C = collections.Counter
    ch, co, alone, alone_hit, used_hit = C(), C(), C(), C(), C()
    ctx_changes = dec_changes = points = 0
    by_purpose = C()
    for (run, purpose), seq in seqs.items():
        for prev, cur in zip(seq, seq[1:]):
            points += 1
            pv, cv = dict((x[0], x[1:]) for x in prev[0]), dict((x[0], x[1:]) for x in cur[0])
            dec_changed = prev[2] != cur[2]
            ctx_changes += prev[1] != cur[1]
            dec_changes += dec_changed
            by_purpose[purpose] += dec_changed
            changed = [n for n in set(pv) | set(cv) if pv.get(n) != cv.get(n)]
            for n in changed:
                k = (purpose, n.split("[")[0] if n.startswith("tool[") else n)
                ch[k] += 1
                co[k] += dec_changed
                used_hit[k] += dec_changed and (n in prev[3] or n in cur[3])
                if len(changed) == 1:
                    alone[k] += 1
                    alone_hit[k] += dec_changed
    rates = {f"{p}/{n}": {"state_changes": ch[(p, n)], "with_decision_change": co[(p, n)],
                          "state_impact_rate": co[(p, n)] / ch[(p, n)],
                          "used_impact_rate": used_hit[(p, n)] / ch[(p, n)],
                          "alone_changes": alone[(p, n)],
                          "alone_impact_rate": (alone_hit[(p, n)] / alone[(p, n)]) if alone[(p, n)] else None}
             for (p, n) in sorted(ch)}
    return {"points": points, "material_context_changes": ctx_changes, "decision_changes": dec_changes,
            "decision_changes_by_purpose": dict(by_purpose), "by_state": rates}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "eval" / "results" / "policy_impact.json"))
    a = ap.parse_args(argv)
    recs = load()
    E, seqs, dg = replay(recs)
    _, _, dg2 = replay(recs)
    out = {"runs": len({r for r, _ in seqs}), "deterministic": dg == dg2, "decision_digest": dg[:16],
           "assumptions": {"capabilities": CAP, "external_labels": LABELS.name, "builder": BUILDER_VERSION,
                           "purposes": {p: PURPOSES[p].version for p, _ in POLICIES},
                           "policies": [pol.NAME for _, pol in POLICIES] + ["(시험 정책 -- MS 아님)"],
                           "sensor_contract": E.EXPORT_CONTRACT},
           "policy_usefulness": impact(seqs)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: out[k] for k in ("runs", "deterministic", "decision_digest")}, ensure_ascii=False))
    u = out["policy_usefulness"]
    print(f"평가점 {u['points']:,} · 결정 관련 문맥 변화 {u['material_context_changes']:,} · 결정 변화 {u['decision_changes']:,} "
          f"{u['decision_changes_by_purpose']}")


if __name__ == "__main__":
    main()
