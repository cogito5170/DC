# DC — Decision Context

**DC 는 State 를 모아 놓은 JSON 이 아니다.** 지금 결정에 필요한 State 를 **고르고**, 신선도 · 근거 · 제약 · 실행 가능성을
붙여, Policy 가 소비할 **하나의 고정 스냅숏**으로 만드는 계약층이다. 표준 라이브러리만 쓴다.

```
Policy    "무엇을 할까"                 MS: Context · Prompt · Provider Policy, WALP
   ▲
DC        "이번 결정에 무엇이 중요한가"   ← 이 저장소
   ▲
State     "무엇이 일어나고 있나"          Sensor: llmsensor.state · MS: usage_model
   ▲
Telemetry "무엇을 보았나"
```

설계 전체: **[`docs/DECISION_CONTEXT.md`](docs/DECISION_CONTEXT.md)** · 선행조사(원문을 못 읽은 것까지):
[`paper/선행조사/DC.md`](paper/선행조사/DC.md) · 시연: [`examples/demo_output.txt`](examples/demo_output.txt).

## 다섯 단계

```
State 소스들 ─► Select ─► Filter ─► Validate ─► Project ─► Freeze ─► DecisionContext
```

| 단계 | 한 줄 |
|---|---|
| Select | 목적이 부른 상태만. 없어도 빼지 않고 UNKNOWN 으로 남긴다 |
| Filter | 소스마다 받은 '지금' 으로 신선도를 다시 잰다. STALE · UNKNOWN 을 지우지 않고 표시한다 |
| Validate | 신선도 · 완결성 · 일관성(값 집합) · 근거(규칙 · 증거 참조) · 권위(ESTIMATE · 제안 거절). 못 넘으면 강등 |
| Project | 목적별 키 `역할.상태`, 제약 · 능력 · 행동(가능/불가능과 까닭) · 판정 · 출처 |
| Freeze | frozen dataclass, id = 내용 sha256. 뒤의 State 변화가 닿지 않는다 |

## 세 가지 문맥을 섞지 않는다

| | 묻는 것 | 어디 |
|---|---|---|
| System State | 세계가 지금 어떠한가 | Sensor · MS |
| **Decision Context** | 이번 결정을 위해 무엇을 알아야 하나 | **DC** |
| LLM Context | LLM 에게 무엇을 보일까 | MS ContextPolicy · PromptPolicy |

DC 는 상태를 계산하지 않고(State 의 일), 행동을 고르지 않고 목적함수를 갖지 않으며(Policy 의 일), 프롬프트를 짓지 않는다
(Context Policy 의 일).

## 쓰기

```python
from dc import DecisionContextBuilder, SensorSource, MSUsageSource, Constraint, policy_state

sensor = SensorSource(engine)                              # llmsensor.state.StateEngine
ms = MSUsageSource(manager, "usage-model-1")               # ms.manager.StateManager (+ usage_model)
B = DecisionContextBuilder([sensor, ms])

ctx = B.build("provider_selection",
              {**sensor.subject(run_id), "session": "session:s1"},
              now_ms={"sensor": engine_now_ms, "ms": ms.now_ms()},     # 빌더는 시계를 읽지 않는다
              constraints=[Constraint("max_cost_usd", "<=", 0.10)],
              capabilities={"alternate_provider": True, "retry_budget": True})

ctx.id                      # "dc-…" 내용 해시 -- 결정 기록에 남겨 결정과 묶는다
ctx.core_dict()             # 정책에 보내는 것(core) -- 키별 [값 | null, 유효성] · 제약 · 가능 행동. 근거 · 문제는 ctx.provenance
ctx.reuse_key               # as_of 를 뺀 core 의 해시 -- 결정 재사용 열쇠(BD-37)
ctx.validity.complete       # 필수 상태가 전부 판정되었나
ctx.value("runtime.rate_limit_state")   # 쓸 수 있을 때만 값, 아니면 None
ctx.available_actions       # ("KEEP_PROVIDER", "SWITCH_PROVIDER", "RETRY", "STOP")
policy_state(ctx, "session")            # MS 정책 선택기가 받는 꼴 {이름: 값 | None}
```

목적 다섯: `context_runtime`(MS CR 에 꽂는 것) · `context_policy` · `prompt_policy` · `provider_selection` · `execution_control` (`dc/purpose.py`).

```bash
python3 -m unittest discover -s tests -t .      # 79 개. 옆 저장소(../Sensor · ../MS)가 있으면 통합 시험까지
python3 examples/demo.py                         # 진짜 Sensor · MS State 로 시연
python3 eval/policy_impact.py                    # 시험 정책(refpolicy)으로 결정 문맥이 결정을 바꾸나 -- Sensor 레코드 301 실행
```

## 불변식 일곱 (시험이 붙든다, 변이 15 가지로 확인)

1. 원 텔레메트리가 결정 문맥에 들어가지 않는다
2. 상태가 없으면 UNKNOWN 이지 추정값이 아니다
3. STALE 은 VALID 로 바뀌지 않는다
4. 결정 문맥은 만든 뒤 바뀌지 않는다
5. 쓸 수 있는 상태는 모두 근거로 되짚힌다
6. 결정 문맥은 정책을 정하지 않는다
7. LLM 출력은 결정 문맥의 상태를 고치지 못한다

## 알고 쓸 것

- **Sensor 의 결정 문맥을 합쳤다**(baseline PC-08): `allow_stale` · `ContextStore` · 시험 정책(`refpolicy/`, MS 아님) · 정책 쓸모 평가가
  여기로 왔다. 결정 문맥은 이제 이 저장소 하나다. 설계 문서 7.3 절.
- **Sensor 배선**: `SensorSource` 는 Sensor 의 내보내기 계약(`llmsensor.state-export/1`)만 읽는다. 실제 세션으로:
  `python3 examples/sensor_session.py <세션>.jsonl`. **DC 는 아직 바뀐다** -- 바꿔도 되는 것과 계약(못 바꾸는 것)은 설계 문서 7.2 절.
- **MS 런타임 배선**: `Runtime(..., state_reader=MSStateReader(builder, "context_runtime"))`. 두 저장소는 서로 import 하지 않고, MS 의
  `state_reader` 이음매(함수 꼴 하나)로만 맞물린다. 안 꽂으면 MS 는 예전처럼 `usage_model.snapshot()` 을 쓴다. 자세히: 설계 문서 7 절.
- MS 파생 상태의 시각은 입력 중 가장 오래된 것(예산 설정 포함)이라, MS 상태에 엄한 max_age 를 걸면 늘 STALE 이 된다.
- `reason` 은 DC 에 없다(PC-07). 사람이 읽을 까닭은 소스(Sensor `explain`)에서.
- 낡은 상태를 '모름' 으로 돌리는 것이 더 나은 결정을 낳는지는 **재지 않았다** — 설계 선택이다.
