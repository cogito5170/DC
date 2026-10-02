# 정책 쓸모 -- 결정 문맥이 결정을 바꾸나 (2026-10-02, baseline PC-08 · PC-07 · BD-58 뒤 다시 잼)

원자료: [`results/policy_impact.json`](results/policy_impact.json) · 스크립트: [`policy_impact.py`](policy_impact.py).
입력: Sensor 의 실제 레코드 301 실행(cc_stream 12 · Claude Code JSONL 1 · SWE-agent 288) + SWE-bench Lite 외부 라벨.
경로: Sensor `StateEngine` → 내보내기 계약 `llmsensor.state-export/2` → `dc.SensorSource` → `DecisionContext` → `refpolicy`(시험 정책, MS 아님).

이 측정은 Sensor `eval/ms_end_to_end.py` 의 "정책 쓸모" 부분이었다. Sensor 안의 결정 문맥 · 참조 정책을 DC 로 합치면서(PC-08) 옮겨 왔다.

## 결과

| | Sensor 판 (`llmsensor/decision` + `reference-*-v1`) | DC 판 (`dc` + `dc-test-*-1`) |
|---|---|---|
| 결정론(두 번 돌려 해시 같음) | 예 | 예 |
| 평가점 | 57,873 | 57,873 |
| 결정 관련 문맥 변화 | 639 | 3,071 (BD-58 전 3,080 -- 맥락 목적이 `agent.execution_health` 를 더는 싣지 않는다) |
| 결정 변화 (맥락 · 공급자 · 실행) | 0 · 0 · 483 | 0 · 0 · **484** (Sensor 통합 340ea57 뒤. 그 전 483 -- 아래) |
| `quality_state` → 결정 (state / used / alone) | 0.617 · 0.617 · 0.617 (287) | 0.617 · 0.617 · 0.617 (287) |
| `execution_health` → 결정 (state / used) | 0.353 · 0.353, 혼자 바뀐 점 12 | 0.353 · 0.353, 혼자 바뀐 점 0 |
| `completion_state` → 결정 | 1.0 (300) | 1.0 (300) |
| 맥락 · 공급자 정책의 결정 변화 | 0 | 0 |

## 읽기

- **483 -> 484 (2026-10-02, Sensor 통합 340ea57 을 합친 뒤).** DC 쪽 변경 없이 Sensor 통합 머리만 바꿔도 484 가 나온다(DC · Sensor 의
  미커밋 변경을 치우고 다시 재서 확인). 늘어난 것은 `execution_control/agent.execution_health` 하나다: 상태 변화 17 -> 18, 그중 결정이 바뀐 것
  6 -> 7(도구별 상태의 동반 변화 6 -> 9). Sensor CMD-S8(집계 상태의 근거 시각 = 값을 정한 근거의 시각, BD-57 · BD-63)로 집계가 실행 중에
  낡을 수 있게 된 결과로 본다 -- **어느 실행인지는 특정하지 않았다.** `state-export/2`(CMD-D7)는 이 수를 바꾸지 않는다.

- **결정은 같다.** 같은 레코드에서 같은 483 번이 같은 목적(실행)에서 바뀐다. 결정이 바뀐 까닭도 같다: 실행이 끝났다(`completion_state`) ·
  외부 라벨이 왔다(`quality_state`) · 도구 실패가 생기거나 풀렸다(`execution_health`).
- **문맥 변화가 많아진 것(639 → 3,071)은 DC 가 도구 상태를 도구마다 펼치기 때문이다**(`tool[<이름>].tool_execution_health`, 2,456 변화).
  그 변화는 결정을 거의 안 바꾼다(0.002, 실행 정책은 도구별 상태를 읽지 않는다). 같은 까닭으로 `execution_health` 가 '혼자' 바뀐 점이 0 이 됐다 --
  도구 상태가 늘 같이 바뀐다.
- **처음 돌렸을 때 `quality_state` 영향이 0 이었다.** Sensor 의 근거 종류 `EXTERNAL_LABEL` 이 DC 어휘에 없어 `UNAUTHORIZED_BASIS` 로 거절됐다.
  baseline 이 DUP-13 · PC-14 로 적어 둔 그 문제다. DC 어휘를 Sensor 8 개로 넓혀(PC-14) 고쳤다 -- 이 표는 고친 뒤다.
- 맥락 · 공급자 정책은 이 데이터에서 결정을 한 번도 바꾸지 않는다(두 판 모두). 실행이 짧고, 압축 문턱 · 요금 한도에 닿은 실행이 없다.
  **결정 문맥이 쓸모 있다는 증거로 읽으면 안 된다** -- 실행 정책의 변화도 대부분 실행 끝의 사실(끝남 · 외부 라벨)에서 온다.

## 행동 어휘의 차이

| Sensor 판 | DC 판 | 차이 |
|---|---|---|
| `manage_context`: KEEP_CONTEXT · REDUCE_CONTEXT · COMPACT_CONTEXT(압축 능력 필요) | `agent_context`(BD-58): KEEP · REDUCE · COMPACT(`runtime_compaction`) | 이름만 -- **같은 행동이다.** (처음 옮길 때는 DC 에 런타임 압축 행동이 없어 `context_policy` 의 COMPRESS 로 옮겼었다. baseline BD-58 로 `agent_context` 를 더해 되돌렸다) |
| `select_provider`: STAY_PROVIDER · SWITCH_PROVIDER · WAIT | `provider_selection`: KEEP_PROVIDER · SWITCH_PROVIDER · WAIT(BD-30 으로 더함) · RETRY · STOP | 이름만 |
| `continue_or_stop`: 끝난 실행이면 문맥이 CONTINUE · RETRY · STOP 을 걸렀다 | `execution_control`: 문맥은 상태로 행동을 거르지 않는다(I6). 끝난 실행의 판단은 정책이 한다 | 결정은 같게 나온다 |
