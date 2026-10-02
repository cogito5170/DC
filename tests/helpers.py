"""시험용 소스 · 레코드."""
from dc import DecisionContextBuilder, StateRecord, StaticSource

MIN = 60_000.0
NOW = 1_000_000.0

SENSOR_DOMAINS = {   # llmsensor.state.REGISTRY 의 값 집합과 같게(2026-10-01)
    "context_pressure": ("BELOW_CONTEXT_LIMIT", "BELOW_COMPACTION_THRESHOLD", "ABOVE_COMPACTION_THRESHOLD",
                         "AT_CONTEXT_LIMIT"),
    "execution_health": ("NO_FAILURE_OBSERVED", "RECOVERED_FAILURES", "UNRESOLVED_FAILURES", "NO_TOOL_RUN_YET"),
    "tool_execution_health": ("NO_FAILURE_OBSERVED", "RECOVERED_FAILURES", "UNRESOLVED_FAILURES"),
    "completion_state": ("RUNNING", "ENDED_NORMALLY", "ENDED_BY_LIMIT", "ENDED_WITH_ERROR"),
    "progress_state": ("STALLED", "NO_STALL_DETECTED"),
    "resource_state": ("WITHIN_BUDGET", "BUDGET_EXHAUSTED"),
    "rate_limit_state": ("AVAILABLE", "EXHAUSTED"),
    "runtime_reliability": ("NO_FAILURE_OBSERVED", "FAILURE_OBSERVED"),
}
LEVELS = ("HIGH", "MEDIUM", "LOW")


def rec(source, entity, name, value, status="INFERRED", *, at=NOW - MIN, ttl=10 * MIN, basis="DEFINITIONAL",
        evidence=None, rule=None, permanent=False):
    ev = (f"{entity}/{name}_metric@1",) if evidence is None else tuple(evidence)
    return StateRecord(source, entity, name, value, status, basis, rule_id=rule if rule is not None else name,
                       rule_version=1, evidence_refs=ev, observed_at_ms=at, ttl_ms=ttl,
                       permanent=permanent)


def sensor_source(run="r1"):
    a, t, rt = f"agent:{run}", f"task:{run}", f"runtime:{run}"
    rows = {
        (a, "context_pressure"): rec("sensor", a, "context_pressure", "BELOW_COMPACTION_THRESHOLD",
                                     basis="RUNTIME_DECLARED"),
        (a, "execution_health"): rec("sensor", a, "execution_health", "UNRESOLVED_FAILURES"),
        (a, "resource_state"): rec("sensor", a, "resource_state", None, "NOT_APPLICABLE", evidence=()),
        (t, "progress_state"): rec("sensor", t, "progress_state", None, "UNKNOWN", evidence=()),
        (t, "completion_state"): rec("sensor", t, "completion_state", "RUNNING", basis="RUNTIME_DECLARED"),
        (rt, "rate_limit_state"): rec("sensor", rt, "rate_limit_state", "AVAILABLE", ttl=5 * MIN),
        (rt, "runtime_reliability"): rec("sensor", rt, "runtime_reliability", "NO_FAILURE_OBSERVED"),
        (f"tool:{run}:Bash", "tool_execution_health"): rec("sensor", f"tool:{run}:Bash", "tool_execution_health",
                                                           "RECOVERED_FAILURES"),
        (f"tool:{run}:WebFetch", "tool_execution_health"): rec("sensor", f"tool:{run}:WebFetch",
                                                               "tool_execution_health", "UNRESOLVED_FAILURES"),
    }
    return StaticSource("sensor", rows.values(), SENSOR_DOMAINS, {"config": "test-v1"})


def ms_source(session="session:s1", values=None, at=NOW - MIN):
    vals = {"token_budget_pressure": "HIGH", "context_pressure": "MEDIUM", "latency_pressure": "LOW",
            "task_complexity": "LOW", "answer_reliability": "HIGH", "correction_rate": "LOW", "retry_pressure": "LOW",
            "tool_churn": "LOW"}
    vals.update(values or {})
    rows = [rec("ms", session, n, v, ttl=None, basis="OPERATOR_ASSUMED", at=at,
                evidence=(f"t-{n}-1", f"t-{n}-2")) if v is not None else
            rec("ms", session, n, None, "UNKNOWN", ttl=None, basis="OPERATOR_ASSUMED", evidence=())
            for n, v in vals.items()]
    return StaticSource("ms", rows, {n: LEVELS for n in vals}, {"model": "usage-model-1"})


def subject(run="r1", session="session:s1"):
    return {"agent": f"agent:{run}", "task": f"task:{run}", "runtime": f"runtime:{run}",
            "tool": (f"tool:{run}:Bash", f"tool:{run}:WebFetch"), "session": session}


def builder(sensor=None, ms=None):
    return DecisionContextBuilder([sensor or sensor_source(), ms or ms_source()])
