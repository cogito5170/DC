# Decision Context 설계 -- 2026-10-01

```
Policy    "무엇을 할까"               MS: Provider Policy · CR(Context Runtime) 안의 맥락 · 프롬프트 계획
            └ 그 뒤 Validate · Arbitrate · Guard(MS Arbiter 를 가르는 중, baseline BD-24 · PC-10) -- DC 는 거기에 관여하지 않는다
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
| LLM Context | LLM 에게 무엇을 보일까 | MS CR(`ms/cr.py`: 맥락 · 프롬프트 꼴) | 지시 · 질의 행 · 예시 |

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

## 4. 데이터 꼴 (`dc/model.py` · `dc/project.py`) -- core 와 provenance (2026-10-02, baseline PC-07 · BD-08)

JSON 부터 설계하지 않았다 -- 의미 객체가 먼저이고 JSON 은 `to_dict()` 의 출력일 뿐이다. **저장하는 것은 digest · core · provenance 셋뿐이다.**

```
DecisionContext
  digest              core + provenance 정준 JSON 의 sha256.   id = "dc-" + 앞 16 자 (투영)
  core                ← 정책이 읽는 것. 정책 쪽으로 보낸다(ctx.core_dict())
    purpose · purpose_version
    as_of             {소스: 지금 ms}  -- 소스마다 시각 기준이 다를 수 있다(Sensor: monotonic_ms 가능, MS: 초 -> ms)
    subject           {역할: 실체 | 실체들}
    states            {키: [값 | null, 유효성]}   값은 쓸 수 있을 때만 -- 목적이 그 키에 allow_stale 을 선언했으면 STALE 도
    constraints       [Constraint(name, op ∈ <= >= == in, value, source)]
    actions           [가능한 행동 이름]
  provenance          ← 감사 · 재현 · explain. 같은 저장소에 두고 id 로 가리킨다
    states[]          key · role · entity · name · source · basis · rule_id · rule_version · evidence_refs(참조만)
                      · observed_at_ms · ttl_ms(적용한 TTL, 입력 기록) · permanent · source_status · issues
                      · withheld(core 에 싣지 않은 소스 값 -- 설명용. core 에 값이 있으면 null)
    capabilities      {이름: 스칼라}          missing   {못 하는 행동: [모자란 능력]}
    builder · sources(소스별 판본)
  (투영 -- 저장하지 않는다)
    StateView = core + provenance + age_ms(as_of − observed_at) · freshness · required(목적 명세)
    validity  = complete · usable · uncertain · not_applicable · missing_required · rejected
    actions   = 목적 명세의 행동 전부(가능 · 못 함 + 까닭)
    reuse_key = "rk-" + (as_of 를 뺀 core) 의 해시 -- 결정 재사용 열쇠(BD-37)
```

- **`reason` 은 없다.** 원 수치가 새어 들고(I1) 크기가 core 만큼이었다(baseline SCHEMA §4.4). 사람이 읽을 까닭은 소스의 explain 에서.
- **값은 한 번만 산다:** 쓸 수 있으면 core 에, 아니면 provenance 의 `withheld` 에(시험이 본다).
- **낡은 값은 둘 다 명시해야 쓰인다(CMD-D6):** 목적 명세의 키별 `allow_stale=True`(core 에 실린다) **그리고** 정책의 `ctx.value(key, allow_stale=True)`.
  명세에 없는 키는 정책이 불러도 None 이다. UNKNOWN · INVALID 는 늘 None.
- **투영에는 지은 그 판본의 명세가 필요하다.** 빌더가 붙여 둔 명세(`ctx.spec`, 해시 · 직렬화 밖)를 쓰고, 기록에서 되살렸으면 등록된 목적 중
  이름과 판본이 **둘 다** 맞는 것을 찾는다. 판본이 다르면 투영하지 않는다(LookupError) -- core 읽기(`ctx.value`)는 명세 없이도 된다.
- core · provenance 의 상태는 키 순으로 저장한다 -- 직렬화의 키 순서와 상관없이 되살린 문맥이 같다.

크기(`examples/demo_output.txt` 6 절, Sensor §40 시연 + MS 세션, 정준 JSON):

| 목적 | core | 전체 | core 비율 | (PC-07 전 전체, baseline §4.4) |
|---|---|---|---|---|
| context_runtime | 803 B | 3,603 B | 22.3 % | -- |
| context_policy | 860 B | 4,131 B | 20.8 % | 5,634 B |
| prompt_policy | 820 B | 3,701 B | 22.2 % | 5,144 B |
| provider_selection | 809 B | 3,745 B | 21.6 % | 4,401 B |
| execution_control | 1,156 B | 6,641 B | 17.4 % | 7,205 B |

baseline 이 잰 core(약 10 %)보다 크다 -- 그쪽은 키별 [값, 유효성] 만 셌고, 여기 core 에는 id · as_of · subject(도구 실체 목록) · 제약 · 가능 행동이 함께 있다.

### 4.1 질의형 선택 (2026-10-02, baseline BD-26 · PC-23)

CR 이 그래프를 직접 질의하지 않도록, 결정에 필요한 **실체 행들**도 DC 를 거친다.

```
목적 명세   queries        고정 질의 QueryRef(source, spec, allow_stale)          spec = MS StateQuery 와 같은 칸
            query_sources  요청마다 질의를 받아도 되는 소스(context_runtime: "ms_world")
build(…, queries=[{"source": "ms_world", "name": "hot", "model": "Server", "where": …}])
   └─ 소스.query(spec, now) 가 돌린다 -- DC 는 질의를 돌리지 않는다(MS 는 MSGraphSource 에 run_query 를 주입)
core        queries {이름: {rows: [{id, model, props {속성: [값 | null, 유효성]}, must, edges}], matched, priority, droppable}}
provenance  queries [{name, source, spec(정준 JSON), refs [(행, 속성, 근거 참조, 관측 ms, 유효성)], withheld, issues}]
ctx.rows("hot")  맥락에 쓰기 좋은 꼴 -- 쓸 수 없는 속성은 None 이고 _unusable 에 이름
```

상태와 같은 규칙이다: 낡은 속성 값은 core 에 없다(질의가 `allow_stale` 을 선언해야만 싣는다). 소스가 없거나 터지면 빈 결과 + `NO_SOURCE` ·
`SOURCE_ERROR`. 비스칼라 속성은 `NOT_SCALAR` 로 거절. 질의 칸은 `QUERY_KEYS` 만 받는다. 진짜 MS 세계 그래프에서 MS `run_query` 와 같은 행 ·
같은 값(낡은 것은 None)이 나오는 것을 시험이 본다(`tests/test_queries.py`).

**요청 질의의 `allow_stale`**(CMD-D13 · BD-65): 요청 질의에 `"allow_stale": true` 를 두면 그 질의에서만 낡은 값이 core 에 실린다 --
유효성은 STALE 그대로라 표시가 붙고(`ctx.rows()` 의 `_stale` · `_unusable`), provenance 의 질의 기록에 `allow_stale` 이 남는다.
소스에 가는 명세(spec)에는 들어가지 않는다(DC 가 거르는 일이다). 참/거짓이 아니면 거절.

**MS 쪽**(CMD-M6 통합): CR 은 `state_reader` 가 돌려준 질의 결과로 맥락을 짓고 그래프에 직접 묻지 않는다. `MSStateReader` 가 요청을 받아
질의를 DC 로 돌린다(7 절). 알고 쓸 것: DC core 의 속성은 이름 순이라, DC 를 거친 맥락은 MS 가 직접 물은 맥락과 **내용은 같고 속성 순서가
다르다**(MS 직접 길은 모형 선언 순). 정준 JSON 이 열쇠를 정렬하므로 DC 는 소스 순서를 싣지 않는다.

## 5. 목적 -- 하나의 고정 꼴이 아니라 목적별 투영 (`dc/purpose.py`)

| 목적 | 판본 | 상태(필수 / *선택*) | 받는 제약 | 행동(필요 능력) |
|---|---|---|---|---|
| `context_runtime` | purpose-cr-3 (요청 질의: `ms_world`) | session: token_budget · context · latency · complexity · reliability · correction · retry (MS CR `plan(state)` 한 번이 읽는 것 전부) | max_context_chars · max_output_tokens · tool_permission | MS 맥락 동작 그대로: KEEP · COMPRESS · SUMMARIZE · RETRIEVE(retrieve_tool) · DROP · DEFER(retrieve_tool) |
| `agent_context` | purpose-agent-context-2 (BD-58) | agent: context_pressure / *agent: execution_interruption* | max_context_tokens | KEEP · REDUCE · COMPACT(runtime_compaction) -- 에이전트 런타임 자신의 맥락(Claude Code 자동 압축 등) |
| `context_policy` | purpose-context-3 | session: token_budget_pressure · context_pressure · task_complexity · answer_reliability · correction_rate / *agent: context_pressure · execution_health* | max_context_chars · must_keep | 위와 같음(-1 의 `COMPACT` 는 MS 에 없어 뺐고 `RETRIEVE` 를 더했다) |
| `prompt_policy` | purpose-prompt-2 | session: token_budget · context · latency · complexity · reliability · correction · retry | max_output_tokens · tool_permission | FULL/CONCISE_INSTRUCTION · ADD_EXAMPLES · JSON_SCHEMA_OUTPUT(native_json_schema) · SET_REASONING(reasoning_control) · NARROW_TOOLS |
| `provider_selection` | purpose-provider-4 | runtime: rate_limit_state · runtime_reliability, session: latency_pressure · answer_reliability / *agent: resource_state · latency_state* | max_cost_usd · max_latency_ms · allowed_providers · data_residency | KEEP_PROVIDER · SWITCH_PROVIDER(alternate_provider) · RETRY(retry_budget) · WAIT(BD-30) · STOP |
| `execution_control` | purpose-execution-3 | agent: execution_health, task: progress_state · completion_state, runtime: rate_limit_state / *tool: tool_execution_health(도구마다) · agent: resource_state · execution_interruption · task: quality_state* | max_cost_usd · max_retries · require_tool_confirmation | CONTINUE · RETRY(retry_budget) · ESCALATE(human_reviewer) · STOP |

`context_policy` · `prompt_policy` 의 상태는 MS 의 `AdaptiveContext` · `AdaptivePrompt` 가 실제로 읽는 것과 같다
(그래서 `policy_state(ctx, "session")` 을 그대로 넘길 수 있다).

**문턱을 지어내지 않는다.** 기본 목적은 `max_age_ms` 를 주지 않는다 -- 소스 TTL 을 따른다. 더 엄한 신선도가 필요하면
`purpose.tightened(새 판본, {키: ms})` 로 복제하고, 그 값은 운영자 가정이다(판본이 provenance 에 남는다).

### 5.1 행동은 '가능한 것' 이지 '고른 것' 이 아니다

행동의 가능 여부는 **구조적 능력**(다른 provider 가 설정됐나, 꺼내는 도구가 있나, 사람이 붙어 있나)으로만 정한다.
상태로 거르지 않는다: 요금 한도가 `EXHAUSTED` 여도 `SWITCH_PROVIDER` 는 능력이 있으면 '가능' 이다 -- 바꿀지는 정책이 정한다.
시험: 상태가 달라도 같은 능력이면 행동 목록이 같다.

### 5.2 안전 기본 결정 -- 목적 명세에 판본으로 (2026-10-02, baseline BD-23 · BD-76 · CMD-D12)

`Purpose.default_decision` 은 순서 있는 후보다. 빌더는 그중 **가능한(능력이 있는) 첫 행동**을 core 의 `default_action` 에 싣는다 --
core 의 일부라 문맥 id · `reuse_key` 에 들어간다. 정책 · MS 가 같은 값을 읽는다(`ctx.default_action`, MS 는 `MSStateReader` 반환의
`record.default_action` -- BD-81).

| 목적 | `default_decision` | 사람(`human_reviewer`)이 없으면 |
|---|---|---|
| `context_runtime` · `context_policy` · `agent_context` | KEEP | KEEP |
| `provider_selection` | KEEP_PROVIDER | KEEP_PROVIDER |
| `execution_control` | ESCALATE, STOP | **STOP** |
| `prompt_policy` | FULL_INSTRUCTION (BD-81: BD-23 의 '고정 프롬프트 계획' = MS `FIXED_PROMPT`) | FULL_INSTRUCTION |

규칙(BD-76): 정책은 **필수 상태의 모름(None)을 지나쳐 다른 분기로 가지 않는다.** 필수 키를 쓸 수 없어 규칙이 정해지지 않으면 `default_action` 을
쓴다(`refpolicy.default`, 결정에 `defaulted=True`). 아는 값만으로 정해지는 분기(예산 소진 → STOP 등)는 그 앞에 남아도 된다.
**문맥은 여전히 고르지 않는다**(I6): 기본 결정은 목적이 선언한 값이고, 그것을 쓸지는 정책이 정한다.

### 5.3 목적함수는 문맥에 없다

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
| I9 | 질의 결과도 같은 규칙(PC-23) | `tests/test_queries.py` -- 낡은 속성은 core 에 없음 · 선언한 질의만 싣음 · 요청 질의는 허락된 소스 · 칸만 · 소스 고장은 빈 결과 + 문제 · 비스칼라 거절 · digest 가 행을 덮음 · 진짜 MS run_query 와 같은 행. 변이 7 가지 빨강 |
| I8 | 정책이 읽는 것(core)과 감사 · 재현(provenance)을 섞지 않는다 · `reason` 없음 (PC-07) | `CoreProvenance` -- core 칸 고정, 쓸 수 없는 값은 core 에 없음, 값은 한 번만, reuse_key 는 as_of 를 뺌, 판본 다른 명세로 투영 안 함, provenance 도 digest 가 덮음. 변이 8 가지 빨강 · 무해 대조 초록 |
| I7 | LLM 출력은 결정 문맥의 상태를 고치지 못한다 | `I7_LLMCannotWrite` -- 권위 없는 소스 등록 거절, 쓰는 메서드 없음, ESTIMATE 근거 거절. 통합: Sensor `propose()` 뒤에도 digest 가 같다 |

**시험이 헛돌지 않는지** 코드를 일부러 망가뜨려 봤다(2026-10-01). 변이 15 가지(배선 뒤 리더 3 · SensorSource 4 · PC-08 로 옮긴 것 7 · PC-07 8 가지 더) -- 소스 예외를 기본값으로 메움 · TTL 을 넘겨도
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
   └─ state_reader(usage_manager, sid, request) -> {"state": {상태: 값 | None}, "record": {...}, "queries": {...}}
         = dc.MSStateReader(builder, "context_runtime")                                       ← MS 의 이음매(기본: usage_model.snapshot)
              └─ DecisionContextBuilder.build(…, queries=request["queries"])  ->  DecisionContext (고정, id)
              └─ policy_state(ctx, "session")      쓸 수 있는 상태만 값, STALE · INVALID · UNKNOWN 은 None
              └─ queries = {이름: core 질의}       CR 이 그래프 대신 이것으로 맥락을 짓는다(CMD-D13)
   └─ ContextRuntime.plan(state)  ·  ProviderPolicy.select(state)                          (MS 정책 규칙은 그대로)
   └─ RunRecord.policy.state       = 위 state                                              (replay 가 그대로 돈다)
      RunRecord.policy.state_source = {kind, id, digest, purpose, purpose_version, complete, uncertain}
```

```python
from dc import DecisionContextBuilder, MSGraphSource, MSUsageSource, MSStateReader
reader = MSStateReader(DecisionContextBuilder([MSUsageSource(usage_manager, U.MODEL_VERSION),
                                               MSGraphSource(world_manager, run_query, StateQuery.from_dict)]),
                       "context_runtime")
rt = Runtime(manager, registry, providers, context_selector=AdaptiveContext(), prompt_selector=AdaptivePrompt(),
             state_reader=reader)
```

MS 쪽 이음매가 지키는 것(MS `tests/test_runtime.py::StateReaderSeam`): 받은 `state` 는 사용 상태 이름(STATES)과 스칼라 값만 통과한다
(원 측정 · 객체를 꽂아 넣으면 ValueError), 출처가 늘 기록된다(기본이면 `usage_model.snapshot`), 재현(`replay`)이 그대로 맞는다.
DC 쪽(`tests/test_integration.py::WithMSRuntime`): 진짜 Runtime(모의 provider)에서 기록의 digest 가 리더의 마지막 문맥과 같고
기록에서 되살려도 맞으며, 압력이 STALE 이면 CR 이 `None` 을 보고 맥락을 줄이지 않는다.

**요청 질의**(CMD-D13): 리더는 요청의 `queries`(dict 또는 질의 데이터클래스, 각 `allow_stale` 포함)를 빌더에 넘기고 core 질의를 `queries` 로
돌려준다 -- MS 는 그것을 꼴 검사한 뒤 결정 기록 `state_source.queries` 에 이름을 남긴다. 목적의 요청 질의 소스(`ms_world`)가 빌더에 없으면
질의를 돌리지 않고 `queries` 를 돌려주지 않는다(빈 결과로 CR 을 굶기지 않는다 -- MS 가 스스로 묻는다). 인자 둘로 불러도 예전 꼴로 돈다.
DC 시험: CR 이 그래프에 묻지 않음(`ms.cr.run_query` 를 막고 돌림) · 낡은 srv04 가 `allow_stale` 없이 None, 있으면 값 + STALE + `_stale` ·
DC 를 거친 맥락의 내용 = MS 직접 길의 내용 · `allow_stale` 은 그 질의에서만.

재현: 기록의 `state_source.digest` 와 `ctx.to_dict()`(리더의 `last` 또는 `sink`)를 맞춰 "그 결정이 본 것" 을 확인한다.

## 7.1 Sensor 와의 배선 -- 내보내기 계약(2026-10-02)

Sensor 가 상태 층 밖으로 내는 길을 하나로 세웠다: `llmsensor.state-export/2`(Sensor `llmsensor/state/export.py`).
`SensorSource` 는 **그 계약만** 읽는다 -- `EXPORT_CONTRACT` · `state_catalog()` · `export_state()` · `subjects()` · `as_of()`.
엔진 안(`current` · `view` · `reg` · `cfg`)은 보지 않는다. 계약 판본이 다르면 추측하지 않고 거절한다.

```
Claude Code JSONL ─► Sensor 수집기(from_cc_jsonl) ─► StateEngine ─► export 계약 ─► dc.SensorSource ─► DecisionContext
python3 examples/sensor_session.py ~/.claude/projects/<프로젝트>/<세션>.jsonl
```

시험: Sensor `tests/test_state_export.py`(칸 · 판본 · 사본 · UNKNOWN · 제안 안 나옴 · 판정기/정책 안 읽음, 변이 3 가지 빨강) ·
DC `tests/test_integration.py::WithSensor`(엔진 안을 감추고 계약 넷만 남겨도 같은 digest · 판본 /2 거절 · 실행 기준 '지금' ·
새 Sensor 상태가 값 집합 안에서 흐름, 변이 4 가지 빨강).

### 이 작업 세션의 기록으로 돌려 본 것 (`examples/sensor_session_output.txt`)

- `execution_control`: `agent.execution_health = UNRESOLVED_FAILURES`(FRESH)인데, 그 근거인 도구 상태 중 미해결인
  `tool[WebFetch]` 는 7 시간 전 것이라 STALE 이다. **신선한 집계 상태가 낡은 구성 요소 위에 서 있다** -- Sensor 의 집계 상태는 새 관측마다
  다시 계산되어 늘 FRESH 이기 때문이다. DC 는 지금 상태끼리 맞대어 보지 않으므로 이것을 잡지 못한다(9 절). 막힌 WebFetch 두 번이 세션
  끝까지 `UNRESOLVED_FAILURES` 를 끌고 가는 것은 Sensor 문서가 이미 적어 둔 한계와 같다.
- `rate_limit_state` · `quality_state` 는 UNKNOWN(Claude Code JSONL 에 그 관측이 없다) -> 필수가 빠져 `complete=False`.
- `provider_selection` 은 MS 소스 없이 지으면 세션 상태가 UNKNOWN 으로 **남는다**(빠지지 않는다).

### /1 -> /2 (2026-10-02, baseline CMD-D7)

| 칸 | /1 | /2 |
|---|---|---|
| `entity_ref` | 없음 | `{type, scope, local}` -- 실체 id 꼴 `<유형>:<범위>:<지역>`(BD-32). scope = 실행 id(불투명), local = 도구 이름 또는 null |
| `time_base` | `as_of` 에만 | 상태마다 (BD-33). DC 는 provenance 의 `time_base` 로 남긴다 |
| `reason` | 있음(원 수치가 든 문자열) | **뺐다** -- 경계 밖으로 수가 새지 않게(BD-08) |
| catalog | 상태 정의 · 유효성 어휘 | + 근거 종류 8 개(`bases`) · 시각 기준 값(`time_bases`) |
| `subjects()` | 역할 -> 실체 | + `scope`(실행 id). DC 는 역할이 아니라서 뺀다 |

유일한 소비자가 DC 라 판본을 함께 올렸다 -- DC `SensorSource` 는 `/2` 만 받는다(`/1` · `/3` 모두 거절, 시험). 엔진의 실체 id 규칙은 그대로다(Sensor 세션의 것).

## 7.2 DC 는 아직 바뀐다 -- 바꿔도 되는 것과 안 되는 것

| | 무엇 | 바꾸면 |
|---|---|---|
| **바꿔도 된다** | 빌더의 다섯 단계 · 검사 · 문제 이름 · `DecisionContext` 꼴 · 목적 표 · 해시 방식 · `summary` | DC 안에서 끝난다. 목적을 바꾸면 그 판본만 올린다 |
| **계약 -- 함부로 못 바꾼다** | Sensor 쪽 `llmsensor.state-export/2`(**DC 세션 소유**, baseline BD-56) | 칸을 빼거나 뜻을 바꾸면 /3 으로 올리고 DC 의 `SensorSource` 를 같이 고친다 |
| **계약 -- 함부로 못 바꾼다** | MS 쪽 `state_reader(um, sid) -> {"state", "record"}`(MS 소유) | 꼴을 바꾸면 MS 와 `MSStateReader` 를 같이 고친다 |

두 저장소 모두 DC 를 import 하지 않는다. 그래서 DC 를 갈아엎어도 Sensor · MS 의 시험은 그대로 초록이다.

## 7.3 Sensor 의 결정 문맥을 합쳤다 (2026-10-02, baseline PC-08 · PC-14)

baseline 이 이 저장소를 결정 문맥의 기준 구현으로 정했다(BD-05). Sensor 안의 결정 문맥 둘(`llmsensor/decision/context` · `StateEngine.decision_context()`)과
참조 정책(`llmsensor/policy`)을 걷어 내고, 쓸 만한 것을 여기로 옮겼다.

| Sensor 에서 | 여기서 |
|---|---|
| `allow_stale` | 목적 명세의 키별 `allow_stale` **그리고** `ctx.value(key, allow_stale=True)` -- 둘 다 명시해야 STALE 값이 쓰인다(CMD-D6 · PC-07 에서 키별 선언으로 고침) |
| `ContextStore` · `explain(context_id)` | `dc.ContextStore`(put · get · dump · load, 변조 거절). 근거 사슬은 문맥마다 복사하지 않는다(BD-05 의 단점, BD-06) -- `evidence_refs` 로 소스에서 펼친다 |
| 목적 넷 | DC 이름으로(BD-30). `WAIT` 을 `provider_selection` 에 더했다. `optimize_llm_request`(목적 셋의 합)는 옮기지 않았다 |
| 참조 정책 `reference-*-v1` | `refpolicy/`(`dc-test-*-2`, CMD-D12 에서 안전 기본 결정을 쓰게 고침) -- DC 기반 시험 정책. `dc` 패키지 밖, 설치되지 않는다. 입력은 `DecisionContext` 하나(시험이 import 를 본다) |
| Phase 8 정책 쓸모 | `eval/policy_impact.py` -- 같은 301 실행에서 결정 변화 **483 번으로 같았다**(옮길 때. 지금은 450 -- [`eval/RESULTS_policy_impact.md`](../eval/RESULTS_policy_impact.md)) |

옮기는 중에 찾은 것: Sensor 의 근거 종류 `EXTERNAL_LABEL`(외부 라벨) · `PROVIDER_DECLARED` · `VALIDATED_EXPERIMENT` 가 DC 어휘에 없어, 외부 라벨
`quality_state` 가 `UNAUTHORIZED_BASIS` 로 거절되고 있었다(처음 돌린 정책 쓸모에서 `quality_state` 영향 0). 근거 어휘를 Sensor 8 개로 넓혔다(PC-14).
기본 허용은 ESTIMATE 만 뺀 일곱이다.

Sensor 판과 다른 것: 끝난 실행에서 CONTINUE · RETRY · STOP 을 문맥이 거르지 않는다(I6) -- 시험 정책이 거른다. 런타임 압축 행동은
목적 `agent_context`(BD-58)로 되찾았다 -- 시험 맥락 정책은 Sensor 판과 같은 KEEP · REDUCE · COMPACT 다.

## 8. 시연에서 본 것 (`examples/demo_output.txt`)

- 같은 State 에서 세 목적이 서로 다른 상태 묶음 · 제약 · 행동을 낸다(맥락 7 · provider 5 · 실행 10 상태).
- 같은 입력이면 같은 id. LLM 이 `execution_health=FAILING` 을 제안해도 id 가 같다.
- 45 분 뒤: 실행 상태들은 STALE(값은 남고 ✗ 표시), 실행 종료는 PERMANENT, `complete=False` · 필수 둘이 빠짐.
- MS `AdaptiveContext` 에 넘기면 지금은 snapshot 과 **같은 계획**(예산 1500). 5 분 뒤 운영자가 압력 상태에 max_age 120 s 를
  건 목적에서는 snapshot 이 여전히 `HIGH` 로 예산을 반으로 줄이고, DC 를 거치면 `None` -> 고정 정책(예산 3000)이다.
  **어느 쪽이 더 나은 결과를 내는지는 재지 않았다** -- 낡은 상태로 줄이지 않는다는 것은 설계 선택이지 측정된 이득이 아니다.

## 9. 알려진 한계 · 하지 않은 것

- (풀림, 2026-10-02 · MS PC-03 · CMD-D9) MS 파생 상태가 예산(설정) 입력 때문에 세션을 연 시각으로 늙던 것. 이제 예산은 운영자 설정
  (`role: config`)이라 파생 시각에 들어가지 않는다 -- 파생 시각은 관측 입력 중 가장 오래된 것이다. 엄한 max_age 를 걸어도 방금 실행이
  있으면 FRESH, 실행이 오래되면 STALE 이다(`test_config_input_no_longer_ages_derived_state` 가 둘 다 본다).
- MS usage model 의 속성에는 TTL 이 없다 -> MS 상태는 신선도가 늘 FRESH(나이는 붙는다). 낡음 판정이 필요하면 목적의
  max_age 가 유일한 길이다(위 한계와 함께).
- `reason` 은 PC-07 로 뺐다. 문제(`issues`)의 `detail` 에는 나이 · 시각 같은 수가 남는다 -- provenance 쪽이고 정책은 읽지 않는다.
- 두 소스의 '같은 현상' 을 맞대어 보는 교차 일관성 검사는 없다(예: Sensor `completion_state=ENDED` 인데 MS 가 아직 진행 중).
  어떤 쌍이 같은 것을 가리키는지의 근거가 아직 없어 규칙을 짓지 않았다. 한 소스 안에서도 마찬가지다: 실제 기록에서 FRESH 인
  `execution_health` 가 STALE 인 도구 상태를 근거로 삼는 것을 봤다(7.1) -- 집계의 신선도는 구성 요소의 신선도를 물려받지 않는다.
- (풀림, BD-58) 런타임 압축 행동이 없던 것: 목적 `agent_context`(KEEP · REDUCE · COMPACT)를 더했다. MS 의 LLM 맥락(`context_policy`)과
  런타임 맥락은 다른 결정이다.
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
