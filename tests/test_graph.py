"""The evidence graph: what each piece of evidence is, where it came from, and what it depends on.

CurrentRelationshipsTest was written before the graph (P6, phase G0) to pin every relationship the
evidence already carries, and where each one lives, so the graph can be checked against them.
"""

from __future__ import annotations

import copy
import random
import unittest

from fpsdet.graph import ACYCLIC, NODE_TYPES, RELATIONS, EvidenceGraph, GraphError, schema

from fpsdet.persist import case_to_dict
from fpsdet.synthetic import build_demo
from fpsdet.week import build_week

DEMO = build_demo()
PLANTED = {case.player_id: case_to_dict(case) for case in DEMO.cases}
WEEK = build_week()
WEEKLY = {case.player_id: case_to_dict(case) for case in WEEK.cases}


def observations(case: dict, kind: str | None = None) -> list[dict]:
    rows = case["evidence"]["observations"]
    return [obs for obs in rows if kind is None or obs["kind"] == kind]


class CurrentRelationshipsTest(unittest.TestCase):
    """Where each relationship lives before the graph makes it explicit."""

    def test_only_the_voice_check_uses_depends_on(self):
        for cases in (PLANTED, WEEKLY):
            kinds = {obs["kind"] for case in cases.values() for obs in observations(case) if obs["depends_on"]}
            self.assertLessEqual(kinds, {"voice"})
        self.assertEqual({obs["kind"] for case in PLANTED.values() for obs in observations(case) if obs["depends_on"]}, {"voice"})

    def test_a_voice_watch_depends_on_its_partners_hidden_mover_on_another_case(self):
        (voice,) = observations(PLANTED["radar-friend"], "voice")
        (hidden,) = observations(PLANTED["wall-eye"], "hidden")
        self.assertEqual(voice["depends_on"], [hidden["observation_id"]])
        self.assertEqual((voice["evidence"]["partner"], voice["evidence"]["party_id"]), ("wall-eye", "stack-radar"))
        self.assertNotIn(hidden["observation_id"], {obs["observation_id"] for obs in observations(PLANTED["radar-friend"])})
        self.assertEqual(voice["match_ids"], ["radar-window"])

    def test_a_shared_leftover_names_its_partner_only_in_its_evidence(self):
        (buyer,) = observations(PLANTED["clone-buyer"], "leftover")
        (source,) = [obs for obs in observations(PLANTED["clone-source"]) if obs["kind"] == "leftover"]
        self.assertEqual((buyer["evidence"]["partner"], source["evidence"]["partner"]), ("clone-source", "clone-buyer"))
        self.assertEqual((buyer["depends_on"], buyer["match_ids"]), ([], []))
        self.assertTrue(buyer["evidence"]["partner_in_review"])

    def test_a_challenge_observation_carries_its_plan_identity_in_its_evidence(self):
        (replay,) = observations(PLANTED["replay-lock"], "occluded_motion_replay")
        challenge = replay["evidence"]["challenge"]
        self.assertEqual((challenge["origin"], challenge["plan"], challenge["commitment"]), ("legacy_private_replay", None, None))
        from tests.test_challenge import follow, plan_for, scored_with

        planned = plan_for()
        (plan,) = planned.plans
        (obs,) = [obs for obs in scored_with(follow(plan, n=16), [planned])["evidence"]["observations"] if obs["family"] == "challenge"]
        self.assertEqual(
            {key: obs["evidence"]["challenge"][key] for key in ("challenge_id", "plan", "commitment", "origin")},
            {"challenge_id": plan.challenge_id, "plan": plan.digest, "commitment": plan.commitment, "origin": "planned"},
        )
        self.assertEqual((obs["key"], obs["match_ids"], obs["subject_id"]), (plan.challenge_id, [plan.match_id], plan.subject_id))

    def test_an_external_observation_carries_its_record_group_and_domain_in_its_evidence(self):
        from tests.test_external import FUSED

        rows = [obs for obs in FUSED["blasted"]["evidence"]["observations"] if obs["family"] == "external"]
        self.assertEqual(len(rows), 2)
        for obs in rows:
            self.assertEqual(obs["key"], obs["evidence"]["external_id"])
            self.assertEqual((obs["evidence"]["provider_group"], obs["evidence"]["telemetry_domain"]), ("example-integrity", "endpoint_memory"))
            self.assertEqual(obs["source"], "external")
        self.assertEqual(FUSED["blasted"]["evidence"]["provenance"]["external"]["records"], 2)

    def test_history_and_cohort_live_in_provenance(self):
        (jump,) = observations(PLANTED["account-changed"], "account_jump")
        self.assertIn("history", jump["evidence"])
        self.assertEqual(PLANTED["account-changed"]["evidence"]["provenance"]["history"]["mode"], "external")
        self.assertEqual(PLANTED["account-changed"]["evidence"]["provenance"]["history"]["windows"], 1)
        self.assertEqual(PLANTED["elite-human"]["evidence"]["provenance"]["history"]["windows"], 0)  # the run had history; this player had none
        for case in PLANTED.values():
            cohort = case["evidence"]["provenance"]["cohort"]
            self.assertEqual((cohort["mode"], cohort["recipe"]), ("external", "fpsdet.cohort/1"))
        baseline = {obs["kind"] for case in PLANTED.values() for obs in observations(case) if obs["family"] == "human_baseline"}
        self.assertEqual(baseline, {"accuracy", "headshot_rate", "median_distance", "rank_tail"})

    def test_every_observation_is_about_its_case_and_its_matches_are_the_cases(self):
        for cases in (PLANTED, WEEKLY):
            for pid, case in cases.items():
                for obs in observations(case):
                    self.assertEqual(obs["subject_id"], pid)
                    self.assertLessEqual(set(obs["match_ids"]), set(case["match_ids"]), (pid, obs["kind"]))

    def test_reports_are_queue_order_only(self):
        streamer = PLANTED["reported-streamer"]
        self.assertEqual((streamer["reports"], streamer["evidence"]["observations"]), (25, []))
        self.assertNotIn("reports", streamer["evidence"])

    def test_the_packet_binds_observation_ids_and_provenance_parts(self):
        from fpsdet.provenance import packet_material

        material = packet_material(PLANTED["radar-friend"])
        self.assertEqual(material["observations"], sorted(obs["observation_id"] for obs in observations(PLANTED["radar-friend"])))
        self.assertEqual(sorted(material["provenance"]), ["cohort", "detector", "external", "history", "inputs", "profile"])


def small_graph(order: int = 0) -> EvidenceGraph:
    """A case with a voice watch resting on a partner's hidden mover, built in a shuffled order."""
    nodes = [
        ("case", "x", {"decision": "watch"}), ("player", "x", {}), ("player", "y", {}), ("match", "m1", {}),
        ("observation", "obs-voice", {"kind": "voice"}), ("observation", "obs-hidden", {"kind": "hidden", "in_case": False}),
    ]
    edges = [
        ("case:x", "about", "player:x"), ("player:x", "played_in", "match:m1"), ("observation:obs-voice", "about", "player:x"),
        ("observation:obs-voice", "supports", "case:x"), ("observation:obs-voice", "names_partner", "player:y"),
        ("observation:obs-voice", "depends_on", "observation:obs-hidden"), ("observation:obs-hidden", "about", "player:y"),
        ("observation:obs-voice", "occurred_in", "match:m1"),
    ]
    rng = random.Random(order)
    rng.shuffle(nodes)
    rng.shuffle(edges)
    graph = EvidenceGraph()
    for node_type, key, attributes in nodes:
        graph.node(node_type, key, **attributes)
    for source, relation, target in edges:
        graph.edge(source, relation, target)
    return graph


class GraphModelTest(unittest.TestCase):
    """The model: closed types and relations, a set of edges, and an identity that ignores order."""

    def test_insertion_order_never_shows(self):
        digests = {small_graph(order).digest() for order in range(20)}
        self.assertEqual(len(digests), 1)
        self.assertEqual(digests.pop(), "sha256:e3acabebb13a75269c914f81e5aec1470cbc92985c3a1c0008887a84c93b1447")

    def test_a_material_change_moves_the_identity(self):
        base = small_graph().digest()
        changed = small_graph()
        changed.node("observation", "obs-extra", kind="hidden")
        self.assertNotEqual(changed.digest(), base)
        rewired = small_graph()
        rewired.edge("observation:obs-voice", "names_partner", "player:x")
        self.assertNotEqual(rewired.digest(), base)

    def test_the_same_edge_twice_is_one_edge_and_a_conflict_is_refused(self):
        graph = small_graph()
        before = graph.digest()
        graph.edge("observation:obs-voice", "depends_on", "observation:obs-hidden")
        self.assertEqual(graph.digest(), before)
        with self.assertRaises(GraphError):
            graph.edge("observation:obs-voice", "depends_on", "observation:obs-hidden", weight=2)
        with self.assertRaises(GraphError):
            graph.node("observation", "obs-voice", kind="leftover")

    def test_only_known_types_relations_and_endpoints(self):
        graph = small_graph()
        refused = [
            lambda: graph.node("vendor", "x"),
            lambda: graph.node("observation", ""),
            lambda: graph.edge("observation:obs-voice", "related_to", "player:y"),
            lambda: graph.edge("observation:obs-voice", "depends_on", "observation:nowhere"),
            lambda: graph.edge("player:x", "depends_on", "observation:obs-voice"),  # a player rests on nothing
            lambda: graph.edge("observation:obs-voice", "provided_by", "player:y"),
            lambda: graph.node("observation", "obs-z", score=0.5),  # no floats
            lambda: graph.node("observation", "obs-z", deep={"a": {"b": {"c": {"d": 1}}}}),
        ]
        for attempt in refused:
            with self.assertRaises(GraphError):
                attempt()

    def test_derivation_cannot_go_round_but_other_relations_are_not_checked_for_cycles(self):
        graph = small_graph()
        graph.edge("observation:obs-hidden", "depends_on", "observation:obs-voice")
        with self.assertRaises(GraphError):
            graph.check_acyclic()
        self.assertEqual(ACYCLIC, {"depends_on", "derived_from"})
        small_graph().check_acyclic()

    def test_a_serialized_graph_is_checked_when_read(self):
        good = small_graph().to_dict()
        self.assertEqual(EvidenceGraph.from_dict(good).digest(), good["digest"])
        broken = {
            "a node listed twice": lambda g: g["nodes"].append(copy.deepcopy(g["nodes"][0])),
            "an edge listed twice": lambda g: g["edges"].append(copy.deepcopy(g["edges"][0])),
            "an edge to nowhere": lambda g: g["edges"].append({"source": "observation:obs-voice", "relation": "depends_on", "target": "observation:gone", "attributes": {}}),
            "an id without its type": lambda g: g["nodes"][0].update(id="nothing"),
            "an unknown relation": lambda g: g["edges"][0].update(relation="related_to"),
            "the wrong digest": lambda g: g.update(digest="sha256:" + "0" * 64),
            "the wrong recipe": lambda g: g.update(recipe="fpsdet.graph/9"),
            "an extra field": lambda g: g["nodes"][0].update(weight=1),
        }
        for name, damage in broken.items():
            with self.subTest(name):
                graph = copy.deepcopy(good)
                damage(graph)
                with self.assertRaises(GraphError):
                    EvidenceGraph.from_dict(graph)

    def test_the_schema_is_data(self):
        described = schema()
        self.assertEqual(set(described["node_types"]), set(NODE_TYPES))
        self.assertEqual(set(described["relations"]), set(RELATIONS))
        self.assertNotIn("related_to", RELATIONS)


if __name__ == "__main__":
    unittest.main()
