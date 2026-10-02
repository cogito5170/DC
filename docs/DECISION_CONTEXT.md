# Decision Context 설계 -- 2026-10-01

```
Policy    "무엇을 할까"               MS: Context · Prompt · Provider Policy, WALP
   ▲
DC        "이번 결정에 무엇이 중요한가"   ← 이 저장소
   ▲
State     "무엇이 일어나고 있나"         Sensor: llmsensor.state.StateEngine · MS: usage_model
   ▲
Telemetry "무엇을 보았나"              Sensor: telemetry 꼴 v2 · MS: RunRecord
```

> **Decision Context 는 State 를 모아 놓은 JSON 이 아니다.** 지금 결정에 필요한 State 를 고르고, 신선도 · 근거 · 제약 ·
> 실행 가능성을 붙여, Policy 가 소비할 수 있는 **하나의 고정 스냅숏**으로 만드는 계약층이다.

선행조사와 그 한계: [`paper/선행조사/DC.md`](../paper/선행조사/DC.md). 시연 출력: [`examples/demo_output.txt`](../examples/demo_output.txt).

## 1. 왜 따로 두나 -- 지금 두 저장소에 있는 것

| | 지금 | 빠진 것 |
|---|---|---|
| Sensor `StateEngine.decision_context(run)` | 상태 값 · 유효성 · 신선도 · 이유. 원 텔레메트리 없음 | **꼴이 하나로 고정**(맥락 · 실행 · 과업 · 자원 · 런타임 전부). 목적이 없다. 제약 · 행동이 없다. id · 해시가 없어 결정과 묶을 수 없다. NOT_APPLICABLE 은 문맥에서 지운다 |
| MS `usage_model.snapshot(manager, sid)` | 파생 상태 여덟 개의 값(HIGH · MEDIUM · LOW · None) | **신선도를 안 본다**(그래프에 남은 값이면 낡아도 낸다). 근거 참조가 없다. 유효성이 None 하나로 뭉개진다 |

두 State 층 모두 "정책이 무엇을 받나" 를 자기 안에서 정했다. DC 는 그것을 한 곳으로 빼서 **목적마다** 같은 규칙으로 짓는다.
State 층은 손대지 않는다 -- 어댑터가 읽기만 한다.

## 2. 세 가지 문맥을 섞지 않는다

| | 묻는 것 | 어디 | 예 |
|---|---|---|---|
| System State | 세계가 지금 어떠한가 | Sensor · MS | `execution_health = UNRESOLVED_FAILURES` |
| **Decision Context** | 이번 정책 결정을 위해 무엇을 알아야 하나 | **DC** | `purpose=provider_selection` · states · constraints · actions |
| LLM Context | LLM 에게 무엇을 보일까 | MS `ContextPolicy` · `PromptPolicy` | 지시 · 질의 행 · 예시 |

Decision Context 를 그대로 프롬프트에 넣지 않는다. 흐름은 `State -> Decision Context -> Context Policy -> LLM Context` 다.
DC 패키지는 프롬프트 · provider 코드를 import 하지 않는다(시험이 본다).

## 3. 다섯 단계 (`dc/builder.py`)

```
State 소스들 ──► 1 Select ──► 2 Filter ──► 3 Validate ──► 4 Project ──► 5 Freeze ──► DecisionContext
```

| 단계 | 하는 일 | 하지 않는 일 |
|---|---|---|
| **1 Select** | 목적이 부른 상태만 읽는다. 역할(agent · task · runtime · tool · session) -> 실체는 `subject` 가 정한다. 도구처럼 실체가 여럿이면 펼친다(`tool[WebFetch].tool_execution_health`) | 소스에 있어도 목적이 안 부른 상태를 넣기. 소스 · 역할이 없거나 소스가 터진 상태를 **빼기**(UNKNOWN + `NO_SOURCE` · `UNBOUND_ROLE` · `SOURCE_ERROR` 로 남긴다) |
| **2 Filter** | 소스마다 받은 **하나의 '지금'** 으로 나이 · 신선도를 다시 잰다. TTL = min(소스 TTL, 목적 `max_age_ms`). 넘으면 STALE | STALE · UNKNOWN 을 지우기. 소스가 STALE 이라 한 것을 FRESH 로 되돌리기(`STALE_AT_SOURCE`). 시계 읽기 |
| **3 Validate** | 아래 다섯 검사. 못 넘으면 **강등**(대개 INVALID)하고 까닭을 `issues` 에 | 고쳐서 받기 · 추정으로 메우기 |
| **4 Project** | 목적별 키 `역할.상태` 로, 목적에 적힌 순서대로. 제약 · 능력 · 행동(가능/불가능과 까닭) · 판정(`Validity`) · 출처(`Provenance`)를 붙인다 | 상태로 행동을 거르기(그것은 정책의 일) |
| **5 Freeze** | frozen dataclass + tuple. id = `dc-` + 내용 sha256 앞 16 자 | 산 State 를 가리키기(live view). 만든 뒤 바뀌기 |

### 3.1 Validate 의 다섯 검사

| 검사 | 문제 이름 | 결과 |
|---|---|---|
| Freshness | `STALE_TTL` · `STALE_PURPOSE` · `STALE_AT_SOURCE` · `UNTIMED_REQUIRED` · `FUTURE_OBSERVATION` | STALE · UNKNOWN(시각이 없는데 목적이 신선도를 요구) · INVALID(관측이 '지금' 보다 뒤) |
| Completeness | -- (문맥 수준) | `validity.complete` = 필수 상태가 전부 *판정됨*(쓸 수 있음 또는 NOT_APPLICABLE). `missing_required` 에 빠진 것 |
| Consistency | `OUT_OF_DOMAIN` · `INCOHERENT` · `NOT_SCALAR` | 값 집합 밖(예: LLM 이 지어낸 `FAILING`) · 쓸 수 있다는데 값이 없음 · 스칼라가 아님(원 텔레메트리 덩어리를 막는다) -> INVALID |
| Provenance | `NO_RULE` · `NO_EVIDENCE` | 값이 있다고 주장하는데 규칙 id 나 근거 참조가 없으면 INVALID -- "되짚을 수 없는 상태는 받지 않는다" |
| Authority | `UNAUTHORIZED_BASIS` + 소스 등록 검사 | 기본으로 `ESTIMATE` 근거는 받지 않는다. `authoritative=True` 가 아닌 소스(LLM 제안 · 의견)는 등록부터 거절 |

같은 이름이 두 State 층에 있어도 섞이지 않는다: Sensor 의 `agent.context_pressure`(런타임 선언 문턱) 와 MS 의
`session.context_pressure`(손으로 둔 0.9 · 0.6 띠)는 **다른 상태**이고 키가 다르다. 같은 키를 두 소스에서 부르는 목적은
명세 단계에서 거절된다.

## 4. 데이터 꼴 (`dc/model.py`)

JSON 부터 설계하지 않았다 -- 의미 객체가 먼저이고 JSON 은 `to_dict()` 의 출력일 뿐이다.

```
DecisionContext
    id · digest          내용 해시(같은 입력 -> 같은 id, 고치면 verify() False)
    purpose              목적 이름
    as_of                {소스: 지금 ms}  -- 소스마다 시각 기준이 다를 수 있다(Sensor: monotonic_ms 가능, MS: 초 -> ms)
    subject              {역할: 실체 | 실체들}
    states[]             StateView
    constraints[]        Constraint(name, op ∈ <= >= == in, value, source)
    capabilities         {이름: 스칼라}
    actions[]            Action(name, available, requires, missing, meaning)
    validity             complete · usable · uncertain · not_applicable · missing_required · rejected
    provenance           builder 판본 · 목적 판본 · 소스별 판본(규칙 · 설정 · 모형)

StateView
    key · role · entity · name · source · required
    value                소스가 준 그대로(STALE · INVALID 여도 남긴다 -- 설명용). 쓸지는 usable 이 정한다
    status               DC 판정 뒤 유효성       source_status   소스가 말한 유효성
    freshness · age_ms · ttl_ms(실제 적용) · observed_at_ms
    basis · rule_id · rule_version · evidence_refs · reason
    issues[]             DC 가 찾은 문제
```

`ctx.value(key)` 와 `policy_state(ctx, role)` 은 **쓸 수 있을 때만** 값을 준다. 나머지는 None(=모름)이다.

## 5. 목적 -- 하나의 고정 꼴이 아니라 목적별 투영 (`dc/purpose.py`)

| 목적 | 판본 | 상태(필수 / *선택*) | 받는 제약 | 행동(필요 능력) |
|---|---|---|---|---|
| `context_runtime` | purpose-cr-1 | session: token_budget · context · latency · complexity · reliability · correction · retry (MS CR `plan(state)` 한 번이 읽는 것 전부) | max_context_chars · max_output_tokens · tool_permission | MS 맥락 동작 그대로: KEEP · COMPRESS · SUMMARIZE · RETRIEVE(retrieve_tool) · DROP · DEFER(retrieve_tool) |
| `context_policy` | purpose-context-2 | session: token_budget_pressure · context_pressure · task_complexity · answer_reliability · correction_rate / *agent: context_pressure · execution_health* | max_context_chars · must_keep | 위와 같음(-1 의 `COMPACT` 는 MS 에 없어 뺐고 `RETRIEVE` 를 더했다) |
| `prompt_policy` | purpose-prompt-1 | session: token_budget · context · latency · complexity · reliability · correction · retry | max_output_tokens · tool_permission | FULL/CONCISE_INSTRUCTION · ADD_EXAMPLES · JSON_SCHEMA_OUTPUT(native_json_schema) · SET_REASONING(reasoning_control) · NARROW_TOOLS |
| `provider_selection` | purpose-provider-1 | runtime: rate_limit_state · runtime_reliability, session: latency_pressure · answer_reliability / *agent: resource_state* | max_cost_usd · max_latency_ms · allowed_providers · data_residency | KEEP_PROVIDER · SWITCH_PROVIDER(alternate_provider) · RETRY(retry_budget) · STOP |
| `execution_control` | purpose-execution-1 | agent: execution_health, task: progress_state · completion_state, runtime: rate_limit_state / *tool: tool_execution_health(도구마다) · agent: resource_state* | max_cost_usd · max_retries · require_tool_confirmation | CONTINUE · RETRY(retry_budget) · ESCALATE(human_reviewer) · STOP |

`context_policy` · `prompt_policy` 의 상태는 MS 의 `AdaptiveContext` · `AdaptivePrompt` 가 실제로 읽는 것과 같다
(그래서 `policy_state(ctx, "session")` 을 그대로 넘길 수 있다).

**문턱을 지어내지 않는다.** 기본 목적은 `max_age_ms` 를 주지 않는다 -- 소스 TTL 을 따른다. 더 엄한 신선도가 필요하면
`purpose.tightened(새 판본, {키: ms})` 로 복제하고, 그 값은 운영자 가정이다(판본이 provenance 에 남는다).

### 5.1 행동은 '가능한 것' 이지 '고른 것' 이 아니다

행동의 가능 여부는 **구조적 능력**(다른 provider 가 설정됐나, 꺼내는 도구가 있나, 사람이 붙어 있나)으로만 정한다.
상태로 거르지 않는다: 요금 한도가 `EXHAUSTED` 여도 `SWITCH_PROVIDER` 는 능력이 있으면 '가능' 이다 -- 바꿀지는 정책이 정한다.
시험: 상태가 달라도 같은 능력이면 행동 목록이 같다.

### 5.2 목적함수는 문맥에 없다

```
State       token_budget_pressure = HIGH          (DC: states)
Constraint  max_cost_usd <= 0.10                  (DC: constraints)
Objective   토큰을 줄여라, 단 품질 >= 문턱         (Policy 소유 -- DC 에 칸이 없다)
```

제약 칸으로 목적함수를 몰래 넣지 못하게: 목적이 받지 않는 제약 이름은 거절하고, `minimize` · `maximize` · `prefer` ·
`weight` · `priority` · `objective` · `score` 같은 낱말이 든 이름은 목적 명세에서도 빌더에서도 거절한다.

## 6. 불변식과 그것을 붙드는 시험

| # | 불변식 | 시험 (`tests/test_dc.py` · `tests/test_integration.py`) |
|---|---|---|
| I1 | 원 텔레메트리가 결정 문맥에 들어가지 않는다 | `I1_NoRawTelemetry` -- 직렬화한 키에 토큰 · 캐시 · 관측 이름 없음, 비스칼라 값은 INVALID, `StateRecord` 에 측정 칸이 없음. 통합: 진짜 Sensor 문맥에 `130000` · `input_tokens` 없음 |
| I2 | 상태가 없으면 UNKNOWN 이지 추정값이 아니다 | `I2_UnknownIsNotEstimated` -- 소스 없음 · 역할 없음 · 소스 예외 · 다른 상태를 준 소스 모두 값 None 인 UNKNOWN 으로 **남는다** |
| I3 | STALE 은 VALID 로 바뀌지 않는다 | `I3_StaleNeverValid` -- TTL 초과 · 소스의 STALE · 목적의 max_age · 시각 없음 + 신선도 요구. `policy_state` 가 None 을 준다. 통합: 45 분 뒤 STALE, 실행 종료는 PERMANENT |
| I4 | 결정 문맥은 만든 뒤 바뀌지 않는다 | `I4_Immutable` -- frozen, 뒤의 State 변화가 앞 문맥에 안 닿음, `object.__setattr__` 변조를 `verify()` 가 잡음, 고친 기록은 `from_dict` 가 거절 |
| I5 | 쓸 수 있는 상태는 모두 근거로 되짚힌다 | `I5_Traceable` -- 근거 · 규칙 없는 상태는 INVALID. 통합: 근거 참조가 Sensor 지표 id 이고 `observation_ids` 로 관측까지 펼쳐짐, MS 는 텔레메트리 id |
| I6 | 결정 문맥은 정책을 정하지 않는다 | `I6_NoPolicyInside` -- 목적함수 · 고른 행동 칸 없음, 목적함수 낱말 제약 거절(명세를 우회해도 빌더가 막음), 행동은 상태와 무관, 패키지가 정책 · 프롬프트 · provider 를 import 안 함 |
| I7 | LLM 출력은 결정 문맥의 상태를 고치지 못한다 | `I7_LLMCannotWrite` -- 권위 없는 소스 등록 거절, 쓰는 메서드 없음, ESTIMATE 근거 거절. 통합: Sensor `propose()` 뒤에도 digest 가 같다 |

**시험이 헛돌지 않는지** 코드를 일부러 망가뜨려 봤다(2026-10-01). 변이 15 가지(배선 뒤 리더 3 가지 더) -- 소스 예외를 기본값으로 메움 · TTL 을 넘겨도
STALE 로 안 바꿈 · 소스 STALE 을 되살림 · 다리가 STALE 값을 넘김 · frozen 끔 · digest 가 상태를 안 봄 · 근거 없음을 받음 ·
목적함수 낱말을 받음 · 행동을 상태로 거름 · 권위 없는 소스 등록 · ESTIMATE 를 권위로 · 비스칼라 통과 · 값 집합 검사 끔 ·
미래 관측 받음 · 목적 밖 상태를 끼움 -- 모두 빨개진다. 무해 대조(주석만 바꿈)는 초록으로 남는다. 처음 돌렸을 때
"목적함수 낱말을 받음" 은 **초록이었다**(목적이 받는 제약 목록이 같은 것을 한 번 더 막아서 시험이 두 검사를 못 갈랐다) --
명세를 우회한 목적으로 빌더의 검사만 따로 보는 시험을 더해 빨개졌다.

## 7. 실행 흐름에서의 자리 -- MS 런타임에 배선했다(2026-10-02)

MS 가 CR(Context Runtime, `ms/cr.py`)을 세운 뒤(cr-1), MS 계획의 ③ Telemetry → DC · ④ DC → CR 자리에 꽂았다.
**두 저장소는 서로를 import 하지 않는다.** 함수 꼴 하나로만 맞물린다:

```
MS Runtime.handle(request)
   └─ state_reader(usage_manager, sid) -> {"state": {상태: 값 | None}, "record": {...}}     ← MS 의 이음매(기본: usage_model.snapshot)
         = dc.MSStateReader(builder, "context_runtime")
              └─ DecisionContextBuilder.build(…)  ->  DecisionContext (고정, id)
              └─ policy_state(ctx, "session")      쓸 수 있는 상태만 값, STALE · INVALID · UNKNOWN 은 None
   └─ ContextRuntime.plan(state)  ·  ProviderPolicy.select(state)                          (MS 정책 규칙은 그대로)
   └─ RunRecord.policy.state       = 위 state                                              (replay 가 그대로 돈다)
      RunRecord.policy.state_source = {kind, id, digest, purpose, purpose_version, complete, uncertain}
```

```python
from dc import DecisionContextBuilder, MSUsageSource, MSStateReader
reader = MSStateReader(DecisionContextBuilder([MSUsageSource(usage_manager, U.MODEL_VERSION)]), "context_runtime")
rt = Runtime(manager, registry, providers, context_selector=AdaptiveContext(), prompt_selector=AdaptivePrompt(),
             state_reader=reader)
```

MS 쪽 이음매가 지키는 것(MS `tests/test_runtime.py::StateReaderSeam`): 받은 `state` 는 사용 상태 이름(STATES)과 스칼라 값만 통과한다
(원 측정 · 객체를 꽂아 넣으면 ValueError), 출처가 늘 기록된다(기본이면 `usage_model.snapshot`), 재현(`replay`)이 그대로 맞는다.
DC 쪽(`tests/test_integration.py::WithMSRuntime`): 진짜 Runtime(모의 provider)에서 기록의 digest 가 리더의 마지막 문맥과 같고
기록에서 되살려도 맞으며, 압력이 STALE 이면 CR 이 `None` 을 보고 맥락을 줄이지 않는다.

재현: 기록의 `state_source.digest` 와 `ctx.to_dict()`(리더의 `last` 또는 `sink`)를 맞춰 "그 결정이 본 것" 을 확인한다.

## 8. 시연에서 본 것 (`examples/demo_output.txt`)

- 같은 State 에서 세 목적이 서로 다른 상태 묶음 · 제약 · 행동을 낸다(맥락 7 · provider 5 · 실행 10 상태).
- 같은 입력이면 같은 id. LLM 이 `execution_health=FAILING` 을 제안해도 id 가 같다.
- 45 분 뒤: 실행 상태들은 STALE(값은 남고 ✗ 표시), 실행 종료는 PERMANENT, `complete=False` · 필수 둘이 빠짐.
- MS `AdaptiveContext` 에 넘기면 지금은 snapshot 과 **같은 계획**(예산 1500). 5 분 뒤 운영자가 압력 상태에 max_age 120 s 를
  건 목적에서는 snapshot 이 여전히 `HIGH` 로 예산을 반으로 줄이고, DC 를 거치면 `None` -> 고정 정책(예산 3000)이다.
  **어느 쪽이 더 나은 결과를 내는지는 재지 않았다** -- 낡은 상태로 줄이지 않는다는 것은 설계 선택이지 측정된 이득이 아니다.

## 9. 알려진 한계 · 하지 않은 것

- **MS 의 파생 시각은 입력 중 가장 오래된 것**이다. 예산(설정)도 입력이라, 실행이 방금 있어도 세션을 연 시각으로 늙는다.
  기본 목적은 max_age 를 주지 않아 영향이 없지만, MS 상태에 엄한 목적을 걸면 늘 STALE 이 된다(`test_known_limit_...` 가
  붙든다). 고치려면 MS 쪽에서 설정 입력을 시각 계산에서 빼야 한다 -- DC 가 소스의 시각을 다시 해석하지 않는다.
- MS usage model 의 속성에는 TTL 이 없다 -> MS 상태는 신선도가 늘 FRESH(나이는 붙는다). 낡음 판정이 필요하면 목적의
  max_age 가 유일한 길이다(위 한계와 함께).
- `reason` 문자열 안의 수(예: `사용률 0.62 < 1`, `맥락 137,000 < 144,000`)는 남는다 -- Sensor `decision_context()` 와 같은
  예외다. 정책은 `reason` 을 해석하지 않는다(사람 · 로그용).
- 두 소스의 '같은 현상' 을 맞대어 보는 교차 일관성 검사는 없다(예: Sensor `completion_state=ENDED` 인데 MS 가 아직 진행 중).
  어떤 쌍이 같은 것을 가리키는지의 근거가 아직 없어 규칙을 짓지 않았다.
- **배선은 선택이다.** `state_reader` 를 안 주면 MS 는 예전처럼 `usage_model.snapshot()` 을 쓴다. CLI(`python3 -m ms ask`) ·
  평가 하니스(`ms eval`)에는 아직 리더를 꽂는 옵션이 없다 -- 사전등록 칸을 바꾸는 일이라 MS 쪽 결정이 먼저다.
- `RunRecord.policy` 에 결정 · 상태를 남기는 것은 L0 Telemetry 의 경계 점검(cogito5170/Telemetry `docs/TELEMETRY.md` 7 절)에 어긋난다고
  MS 계획에 이미 적혀 있다. `state_source` 도 같은 칸에 붙였으므로, 그 칸을 결정 기록으로 뗄 때 함께 옮긴다.
- 리더의 문맥은 MS 사용 상태만 본다(`context_runtime`). Sensor 상태를 CR 에 함께 주려면 리더에 `subject` 와 Sensor 소스 · '지금' 을
  더하면 되지만, MS 정책 선택기는 Sensor 상태 이름을 읽지 않고 MS 이음매도 STATES 밖의 이름을 거절한다 -- 그것은 MS 계획 ②③ 의 일이다.
- MS 초반(실행 3 개 전)에는 `answer_reliability` · `correction_rate` 가 UNKNOWN 이라(usage-model-2 의 표본 문턱) 문맥이 `complete=False` 다.
  MS 정책은 그것을 고정대로 읽으므로 동작은 같다.
- 목적의 상태 · 행동 목록은 손으로 정했다(MS 정책이 읽는 것 + Sensor 상태의 `decision` 칸에서). 판본을 올려 바꾼다.
- Provider 선택 목적의 상태로 무엇이 좋은 선택을 가리키는지는 모른다(MS 도 아직 명시 선택뿐이다).
- 스레드 안전하지 않다. 빌더는 소스를 차례로 읽으므로, 소스가 읽는 사이에 바뀌면 한 문맥 안의 상태들이 서로 다른 순간일
  수 있다(각 상태의 `observed_at_ms` · `age_ms` 로 드러나긴 한다).
