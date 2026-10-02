"""PC-23 (baseline BD-26): 질의형 선택 -- 소스가 질의를 돌리고, DC 는 행을 검사해 core(값 · 유효성) / provenance(근거 · 막힌 값)로 고정한다."""
import json
import unittest

from dc import PURPOSES, DecisionContextBuilder, PurposeError, QueryRef, from_dict
from dc.snapshot import SnapshotError

from .helpers import NOW, ms_source
from .test_integration import MS, msmanager, msusage


class FakeWorld:
    name, authoritative = "ms_world", True

    def __init__(self, rows=None, boom=False):
        self.rows, self.boom, self.calls = rows, boom, []

    def read(self, e, n, now):
        from dc import StateRecord
        return StateRecord(self.name, e, n, None, "UNKNOWN", "OBSERVED")

    def domain(self, e, n):
        return None

    def versions(self):
        return {"graph": "fake"}

    def query(self, spec, now):
        self.calls.append((spec, now))
        if self.boom:
            raise RuntimeError("graph down")
        rows = self.rows if self.rows is not None else [
            {"id": "srv07", "model": "Server", "must": True, "edges": [("in", "srv07", "rack1")],
             "props": {"temp_c": {"value": 97.0, "status": "OBSERVED", "ref": "t12", "observed_at_ms": NOW - 1000},
                       "status": {"value": "critical", "status": "INFERRED", "ref": "derived:temp_c"},
                       "fan_rpm": {"value": 1200, "status": "STALE", "ref": "t3", "observed_at_ms": NOW - 9e6}}},
            {"id": "srv05", "model": "Server", "props": {"temp_c": {"value": 70.0, "status": "OBSERVED", "ref": "t9"}}}]
        return {"rows": rows, "matched": 7}


HOT = {"name": "hot", "model": "Server", "where": [["status", "in", ["hot", "critical"]]], "priority": 0,
       "droppable": [["status", "==", "normal"]]}


def b(world=None):
    return DecisionContextBuilder([ms_source(), world or FakeWorld()])


def build(world=None, purpose="context_runtime", **kw):
    return b(world).build(purpose, {"session": "session:s1"}, now_ms=NOW, **kw)


class Queries(unittest.TestCase):
    def test_request_query_lands_in_core_with_unusable_values_withheld(self):
        ctx = build(queries=[HOT])
        q = ctx.query("hot")
        self.assertEqual((q.matched, [r.id for r in q.rows], q.priority), (7, ["srv07", "srv05"], 0))
        rows = ctx.rows("hot")
        self.assertEqual((rows[0]["temp_c"], rows[0]["status"], rows[0]["fan_rpm"]), (97.0, "critical", None))
        self.assertEqual(rows[0]["_unusable"], ["fan_rpm"])
        self.assertEqual(rows[0]["_edges"], [["in", "srv07", "rack1"]])
        pv = ctx.provenance.queries[0]
        self.assertIn(("srv07", "fan_rpm", 1200), pv.withheld)
        self.assertIn(("srv07", "temp_c", "t12", NOW - 1000, "OBSERVED"), pv.refs)
        core = json.dumps(ctx.core_dict())
        self.assertNotIn("t12", core)                  # 근거 참조는 provenance 에만
        self.assertNotIn("1200", core)                 # 낡은 값은 core 에 없다
        self.assertEqual(ctx.core_dict()["queries"]["hot"]["droppable"], [["status", "==", "normal"]])

    def test_allow_stale_must_be_declared_on_the_query(self):
        P = PURPOSES["context_runtime"].with_(version="purpose-cr-2-q", query_sources=(),
                                              queries=(QueryRef.of("ms_world", HOT, allow_stale=True),))
        ctx = b().build(P, {"session": "session:s1"}, now_ms=NOW)
        self.assertEqual(ctx.rows("hot")[0]["fan_rpm"], 1200)
        self.assertIn("fan_rpm", ctx.rows("hot")[0]["_unusable"])      # 값은 있어도 STALE 이라고 표시한다

    def test_request_queries_only_from_allowed_sources_and_known_keys(self):
        with self.assertRaises(PurposeError):
            build(purpose="execution_control", queries=[HOT])          # 이 목적은 요청 질의를 받지 않는다
        with self.assertRaises(PurposeError):
            build(queries=[dict(HOT, source="sensor")])
        with self.assertRaises(PurposeError):
            build(queries=[dict(HOT, sql="DROP TABLE")])
        with self.assertRaises(PurposeError):
            build(queries=[HOT, HOT])

    def test_missing_or_broken_source_gives_empty_result_with_issue(self):
        ctx = DecisionContextBuilder([ms_source()]).build("context_runtime", {"session": "session:s1"}, now_ms=NOW,
                                                          queries=[HOT])
        self.assertEqual((ctx.query("hot").rows, ctx.provenance.queries[0].issues[0].code), ((), "NO_SOURCE"))
        ctx = build(FakeWorld(boom=True), queries=[HOT])
        self.assertEqual(ctx.provenance.queries[0].issues[0].code, "SOURCE_ERROR")

    def test_non_scalar_prop_is_refused(self):
        w = FakeWorld(rows=[{"id": "x", "model": "M", "props": {"blob": {"value": {"tokens": 1}, "status": "OBSERVED"}}}])
        ctx = build(w, queries=[HOT])
        self.assertEqual(ctx.query("hot").rows[0].props, (("blob", None, "INVALID"),))
        self.assertEqual(ctx.provenance.queries[0].issues[0].code, "NOT_SCALAR")

    def test_frozen_hashed_round_trip(self):
        ctx = build(queries=[HOT])
        d = json.loads(json.dumps(ctx.to_dict()))
        self.assertEqual(from_dict(d), ctx)
        d["core"]["queries"]["hot"]["rows"][0]["props"]["temp_c"][0] = 20.0
        with self.assertRaises(SnapshotError):
            from_dict(d)
        self.assertNotEqual(build(queries=[HOT]).reuse_key, build().reuse_key)

    def test_source_gets_the_spec_and_its_now(self):
        w = FakeWorld()
        build(w, queries=[HOT])
        spec, now = w.calls[0]
        self.assertEqual((spec["name"], now), ("hot", NOW))
        self.assertNotIn("source", spec)


@unittest.skipIf(msmanager is None, f"MS 저장소가 없다: {MS}")
class OnRealMSGraph(unittest.TestCase):
    def setUp(self):
        from ms.query import StateQuery, run_query
        from dc import MSGraphSource, MSUsageSource
        spec = json.loads((MS / "ms/examples/datacenter.json").read_text(encoding="utf-8"))
        self.clock = [float(spec["now"])]
        self.m = msmanager.StateManager.from_spec(spec, clock=lambda: self.clock[0])
        for line in (MS / "ms/examples/datacenter_telemetry.jsonl").read_text(encoding="utf-8").splitlines():
            self.m.ingest(json.loads(line))
        sid = msusage.open_session(self.m, "s", {"token_budget": 300})
        self.sid, self.queries, self.run_query, self.SQ = sid, spec["queries"], run_query, StateQuery
        self.world = MSGraphSource(self.m, run_query, StateQuery.from_dict)
        self.B = DecisionContextBuilder([MSUsageSource(self.m, msusage.MODEL_VERSION), self.world])

    def build(self):
        return self.B.build("context_runtime", {"session": self.sid},
                            now_ms={"ms": self.m.clock() * 1000, "ms_world": self.world.now_ms()}, queries=self.queries)

    def test_same_rows_as_ms_run_query(self):
        ctx = self.build()
        for q in self.queries:
            direct = self.run_query(self.SQ.from_dict(q), self.m)
            got = ctx.query(q["name"])
            self.assertEqual([r.id for r in got.rows], [r.id for r in direct.rows], q["name"])
            self.assertEqual(got.matched, direct.matched)
            for r_dc, r_ms in zip(ctx.rows(q["name"]), direct.rows):
                for p, v in r_ms.props.items():
                    self.assertEqual(r_dc[p], None if v["stale"] else v["value"], (r_ms.id, p))

    def test_staleness_follows_ms(self):
        self.clock[0] += 10_000                   # 세계 속성의 ttl 을 넘긴다
        ctx = self.build()
        stale = [(r.id, p) for q in ctx.core.queries for r in q.rows for p, v, st in r.props if st == "STALE"]
        self.assertTrue(stale)
        self.assertTrue(all(v is None for q in ctx.core.queries for r in q.rows for p, v, st in r.props
                            if st == "STALE"))


if __name__ == "__main__":
    unittest.main()
