"""CMD-NET3 (baseline POL-3 BD-309): peer_interaction 목적과 PeerSource -- 이웃 상태도 소스, 같은 Validate."""
import json
import tempfile
import unittest
from pathlib import Path

from dc import PURPOSES, DecisionContextBuilder, PeerSource, StateRecord, StaticSource
from dc import peer_capabilities, peer_interaction_purpose
from dc.sources import SourceError

from .helpers import MIN, NOW, rec

OWN = "agent.execution_health"


def export(j, **over):
    d = {"entity": "agent:r1", "name": "execution_health", "value": "NO_FAILURE_OBSERVED", "status": "INFERRED",
         "basis": "DEFINITIONAL", "rule_id": "execution_health", "rule_version": 1,
         "evidence_refs": ["agent:r1/x@1"], "observed_at": NOW - MIN, "ttl_ms": 10 * MIN, "final": False,
         "since": None, "time_base": "unix_ms"}
    d.update(over)
    return {"contract": "llmsensor.state-export/2", "node": j, "states": [d]}


def build(peers, missing=(OWN,), uncertain=(), caps=None, own=()):
    own_src = StaticSource("sensor", own)
    net = StaticSource("net", [rec("net", f"pi:{j}", "", 0.5) for j in peers.nodes])
    b = DecisionContextBuilder([own_src, peers, net])
    P = peer_interaction_purpose(missing, uncertain)
    subj = {"agent": "agent:r1", "peer": tuple(PeerSource.entity(j) for j in peers.nodes),
            "pi": tuple(f"pi:{j}" for j in peers.nodes)}
    return b.build(P, subj, now_ms=NOW, capabilities=peer_capabilities(peers, missing, peer_link=True, base=caps))


def state(ctx, key):
    return next(s for s in ctx.core.states if s.key == key)


class Spec(unittest.TestCase):
    def test_purpose_is_data(self):
        P = PURPOSES["peer_interaction"]
        self.assertEqual([a.name for a in P.actions], ["consult", "send", "skip"])
        self.assertEqual(P.default_decision, ("skip",))

    def test_refs_keys(self):
        ctx = build(PeerSource({"j": export("j")}), uncertain=("task.progress_state",))
        keys = {s.key for s in ctx.core.states}
        self.assertEqual(keys, {OWN, "task.progress_state", f"peer[j].{OWN}", "peer[j].task.progress_state", "pi[j]"})
        self.assertIn(("agent.execution_health", True), [(r.key(), r.required) for r in
                      peer_interaction_purpose((OWN,)).refs if r.required])


class PeerValidate(unittest.TestCase):
    def test_good_peer_value_usable_and_unchanged(self):
        ctx = build(PeerSource({"j": export("j")}))
        s = state(ctx, f"peer[j].{OWN}")
        self.assertEqual((s.value, s.status), ("NO_FAILURE_OBSERVED", "INFERRED"))
        p = next(x for x in ctx.provenance.states if x.key == s.key)
        self.assertEqual(p.basis, "DEFINITIONAL")

    def test_evidence_less_peer_value_is_invalid(self):
        ctx = build(PeerSource({"j": export("j", evidence_refs=[])}))
        s = state(ctx, f"peer[j].{OWN}")
        self.assertEqual((s.value, s.status), (None, "INVALID"))

    def test_stale_peer_value_not_usable(self):
        ctx = build(PeerSource({"j": export("j", observed_at=NOW - 20 * MIN)}))
        s = state(ctx, f"peer[j].{OWN}")
        self.assertIsNone(s.value)
        self.assertIn(s.status, ("STALE", "INVALID"))

    def test_unauthorized_basis_invalid(self):
        ctx = build(PeerSource({"j": export("j", basis="ESTIMATE")}))
        self.assertEqual(state(ctx, f"peer[j].{OWN}").status, "INVALID")

    def test_wrong_contract_refused(self):
        with self.assertRaises(SourceError):
            PeerSource({"j": {**export("j"), "contract": "llmsensor.state-export/1"}})

    def test_from_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.json"
            p.write_text(json.dumps(export("j")))
            src = PeerSource.from_files([p])
        self.assertEqual(sorted(src.nodes), ["j"])


class Decision(unittest.TestCase):
    def test_default_skip_without_missing_or_uncertain(self):
        ctx = build(PeerSource({"j": export("j")}), missing=())
        self.assertEqual(ctx.core.default_action, "skip")
        self.assertEqual({s.key for s in ctx.core.states}, {"pi[j]"})

    def test_default_skip_with_missing(self):
        self.assertEqual(build(PeerSource({"j": export("j")})).core.default_action, "skip")

    def test_consult_only_when_peer_covers_missing(self):
        covered = build(PeerSource({"j": export("j")}))
        self.assertIn("consult", covered.core.actions)
        other = export("j", name="progress_state", entity="task:r1")
        self.assertNotIn("consult", build(PeerSource({"j": other})).core.actions)
        silent = export("j", value=None, status="UNKNOWN")
        self.assertNotIn("consult", build(PeerSource({"j": silent})).core.actions)
        self.assertNotIn("consult", build(PeerSource({"j": export("j")}), missing=()).core.actions)

    def test_no_link_no_consult_no_send(self):
        peers = PeerSource({"j": export("j")})
        caps = peer_capabilities(peers, (OWN,), peer_link=False)
        self.assertFalse(caps["peer_link"])
        b = DecisionContextBuilder([StaticSource("sensor"), peers, StaticSource("net")])
        ctx = b.build(peer_interaction_purpose((OWN,)), {"agent": "agent:r1", "peer": ("peer:j",), "pi": ("pi:j",)},
                      now_ms=NOW, capabilities=caps)
        self.assertEqual(ctx.core.actions, ("skip",))


if __name__ == "__main__":
    unittest.main()
