"""Sensor -> DC 를 실제 데이터로: Claude Code 세션 JSONL 하나를 Sensor 로 읽어 결정 문맥을 짓는다.

    python3 examples/sensor_session.py ~/.claude/projects/<프로젝트>/<세션>.jsonl [--now-ms N] [--out 파일]

    Telemetry(Sensor 수집기 from_cc_jsonl) -> State(Sensor StateEngine) -> 내보내기 계약 -> DC(SensorSource) -> DecisionContext

Sensor 는 DC 를 import 하지 않고, DC 패키지도 Sensor 를 import 하지 않는다. 둘을 잇는 것은 이 스크립트(배선)뿐이다.
출력에는 상태 · 유효성 · 근거 id 만 나온다 -- 질문 · 답 · 명령의 글은 Sensor 수집기가 애초에 담지 않는다(해시 · 길이만).
DC_SENSOR_PATH 로 Sensor 위치를 바꾼다(기본 ../Sensor).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(Path(os.environ.get("DC_SENSOR_PATH", ROOT.parent / "Sensor"))))

from dc import DecisionContextBuilder, SensorSource, summary  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--run", default="cc:session")
    ap.add_argument("--now-ms", type=float, default=None, help="같은 시각 기준(unix_ms)의 '지금'. 없으면 마지막 관측 시각")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    from llmsensor.state import DEFAULT_CONFIG, StateEngine, from_telemetry
    from llmsensor.telemetry.collect import from_cc_jsonl

    recs = from_cc_jsonl(a.session, a.run)
    # Claude Code JSONL 에는 맥락 창이 없다 -- Sensor 문서 값(운영자 가정)으로 채운다. 안 채우면 context_pressure 는 UNKNOWN
    E = StateEngine(DEFAULT_CONFIG.with_(version="cc-jsonl-window200k", context_window_override=200_000))
    E.ingest_all(from_telemetry(recs))
    src = SensorSource(E, run_id=a.run)
    B = DecisionContextBuilder([src])
    now = a.now_ms if a.now_ms is not None else src.now_ms()
    L = [f"Sensor 레코드 {len(recs)} 개 · 실행 {a.run} · as_of {E.as_of(a.run)} · now {now}", ""]
    for purpose, caps in (("execution_control", {"retry_budget": True, "human_reviewer": True}),
                          ("provider_selection", {"alternate_provider": False})):
        L.append(summary(B.build(purpose, src.subject(), now_ms=now, capabilities=caps)))
        L.append("")
    out = "\n".join(L)
    if a.out:
        Path(a.out).write_text(out + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
