# 선행조사: Decision Context -- 2026-10-01

## 이 작업에서 원문을 읽었나

**못 읽었다.** 사용자가 준 세 출처를 이 컨테이너에서 열어 보려 했으나:

| 출처 | 결과 |
|---|---|
| NASA NTRS 20190002780 -- *Model-based System Health Management and Contingency Planning for Autonomous UAS* | 막힘(egress proxy: ntrs.nasa.gov) |
| NASA NTRS 20210008090 -- *Assurance of Model-Based Fault Diagnosis* · 20020066395 -- *Model Based Autonomy for Robust Mars Operations* | 같은 서버, 시도 안 함(위와 같은 이유) |
| Microsoft Learn -- *Agent Pipeline Architecture* (Agent Framework) | 막힘(learn.microsoft.com) |
| Google Cloud -- *What is AI context engineering?* | 가져왔으나 본문이 잘려 정의 · 단계를 확인하지 못함 |

그래서 아래 "가져온 것" 은 **사용자가 요약한 주장**에 기대고 있다. 이 저장소의 설계 근거로 쓴 것은 그 주장 중에서도
코드 · 시험으로 직접 확인할 수 있는 **경계 원칙**뿐이고, 출처의 구체적 구현(특정 클래스 · API)을 따른 곳은 없다.

## 가져온 것 -- 원칙만

| 출처(사용자 요약) | 원칙 | DC 에서 |
|---|---|---|
| NASA: 진단(health)과 결정(decision maker)을 가르고, 결정은 **현재 상태**와 그 상태에서 **가능한 대응(contingency)** 을 본다 | 상태를 만드는 곳 ≠ 상태를 소비하는 곳. 결정자에게는 상태와 가능한 행동이 함께 간다 | DC 는 상태를 계산하지 않는다(소스 어댑터만). `actions` 에 가능한 행동을, 고르는 일은 정책에 |
| NASA: 진단의 품질이 자율성의 신뢰에 직결 | 상태의 품질(유효성 · 신선도 · 근거)이 결정 입력의 일부다 | UNKNOWN · STALE 을 지우지 않고 전달, 근거 없는 상태는 INVALID |
| Microsoft: 에이전트 파이프라인에서 context provider 가 호출 전에 맥락 · 상태를 주입하는 **별도 층** | 맥락을 모으는 층을 오케스트레이터 · 모형 호출과 가른다 | `DecisionContextBuilder` 는 정책 · provider 를 import 하지 않는다 |
| Google: context engineering = 모형 호출 전에 필요한 데이터를 **모으고 · 거르고 · 구조화**하는 파이프라인. 프롬프트와 같지 않다 | 결정 문맥 ≠ LLM 문맥 | Decision Context 와 LLM Context(MS ContextPolicy)를 다른 단계로 둔다 |

## 이미 이 작업 묶음 안에 있던 근거 (읽은 것)

- Sensor `paper/선행조사/STATE층.md` · `docs/STATE_MODEL.md`: K8s Conditions(없음 = Unknown), Prometheus `for`
  (흔들림 억제), 신선도를 질의 때 판정. DC 는 같은 낱말(OBSERVED · INFERRED · UNKNOWN · STALE · INVALID · NOT_APPLICABLE)과
  같은 원칙("모르면 모름", "STALE 은 지금 값이 아니다")을 쓴다.
- Sensor `StateEngine.decision_context()`: 원 텔레메트리 · 지표 값을 넣지 않는 최소 결정 문맥. DC 는 이것을 목적별로 일반화했다.
- MS `ms/policy.py`: 정책은 (기록된 상태, 판본)의 순수 함수이고 모름은 "고정 정책대로". DC 의 `policy_state()` 는 그 꼴을
  그대로 지키되 낡은 · 근거 없는 상태를 모름으로 바꾼다.

## 새것인가

아니다. 상태 스냅숏 + 신선도 + 출처 + 내용 해시는 흔한 묶음이다(데이터 계보 · 이벤트 소싱 · 설정 스냅숏). 여기서 한 일은
두 State 층(Sensor · MS)과 정책 층 사이의 **경계를 코드와 시험으로 못 박은 것**이다.
