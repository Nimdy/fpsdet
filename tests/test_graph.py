"""The evidence graph: what each piece of evidence is, where it came from, and what it depends on.

CurrentRelationshipsTest was written before the graph (P6, phase G0) to pin every relationship the
evidence already carries, and where each one lives, so the graph can be checked against them.
"""

from __future__ import annotations

import copy
import random
import unittest

import json
import os
import tempfile
from pathlib import Path

from fpsdet.graph import ACYCLIC, NODE_TYPES, RELATIONS, EvidenceGraph, GraphError, build_graph, describe, graph_block, schema, verify_graph, verify_graphs

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


def graph_of(case: dict) -> EvidenceGraph:
    return EvidenceGraph.from_dict({key: value for key, value in case["evidence"]["graph"].items() if key != "summary"})


def node_ids(graph: EvidenceGraph, node_type: str) -> list[str]:
    return [node.id for node in graph.of_type(node_type)]


def regraph(case: dict) -> dict:
    """The case with its graph block rebuilt from its own evidence, as case_to_dict writes it."""
    case = copy.deepcopy(case)
    case["evidence"].pop("graph", None)
    case["evidence"]["graph"] = graph_block(case)
    return case


class CaseGraphTest(unittest.TestCase):
    """Every case carries its graph, and every graph holds together."""

    def test_every_planted_and_weekly_graph_verifies(self):
        for cases in (PLANTED, WEEKLY):
            for pid, case in cases.items():
                self.assertEqual(verify_graph(case), [], pid)
            self.assertEqual(verify_graphs(cases.values()), [])

    def test_the_graph_does_not_touch_the_decision(self):
        for case in DEMO.cases:
            row = case_to_dict(case)
            self.assertEqual(row["decision"], case.decision)
            self.assertEqual(graph_of(row).get(f"case:{case.player_id}").attributes, {"decision": case.decision})

    def test_observation_order_does_not_move_the_graph(self):
        case = copy.deepcopy(PLANTED["clone-source"])
        before = case["evidence"]["graph"]["digest"]
        case["evidence"]["observations"].reverse()
        self.assertEqual(build_graph(case).digest(), before)

    def test_a_voice_watch_depends_on_its_partners_hidden_mover(self):
        graph = graph_of(PLANTED["radar-friend"])
        (voice,) = [n.id for n in graph.of_type("observation") if n.attributes.get("kind") == "voice"]
        (hidden,) = [obs["observation_id"] for obs in observations(PLANTED["wall-eye"], "hidden")]
        self.assertEqual(graph.out(voice, "depends_on"), [f"observation:{hidden}"])
        self.assertEqual(graph.get(f"observation:{hidden}").attributes, {"in_case": False})
        self.assertEqual(graph.out(f"observation:{hidden}", "about"), ["player:wall-eye"])
        self.assertEqual(graph.out(voice, "names_partner"), ["player:wall-eye"])
        self.assertEqual(graph.out(voice, "supports"), ["case:radar-friend"])

    def test_a_shared_leftover_names_its_partner(self):
        graph = graph_of(PLANTED["clone-buyer"])
        (leftover,) = [n.id for n in graph.of_type("observation")]
        self.assertEqual(graph.out(leftover, "names_partner"), ["player:clone-source"])
        self.assertEqual(graph.out(leftover, "depends_on"), [])
        source = graph_of(PLANTED["clone-source"])
        self.assertEqual(sorted(t for n in source.of_type("observation") for t in source.out(n.id, "names_partner")), ["player:clone-buyer"])

    def test_history_and_cohort_are_identities_not_copies(self):
        graph = graph_of(PLANTED["account-changed"])
        (jump,) = node_ids(graph, "observation")
        history = PLANTED["account-changed"]["evidence"]["provenance"]["history"]
        self.assertEqual(graph.out(jump, "uses_history"), [f"history:{history['digest']}"])
        self.assertEqual(graph.get(f"history:{history['digest']}").attributes, {"mode": "external", "recipe": "fpsdet.history/1", "windows": 1})
        rage = graph_of(PLANTED["rage"])
        cohort = PLANTED["rage"]["evidence"]["provenance"]["cohort"]["digest"]
        for name in node_ids(rage, "observation"):
            self.assertEqual(rage.out(name, "compared_against"), [f"cohort:{cohort}"])
        self.assertEqual(node_ids(graph_of(PLANTED["elite-human"]), "cohort"), [])  # nothing of its was compared into a finding

    def test_a_legacy_replay_is_a_challenge_with_no_plan(self):
        graph = graph_of(PLANTED["replay-lock"])
        (replay,) = node_ids(graph, "observation")
        (challenge,) = graph.out(replay, "derived_from")
        self.assertEqual(challenge, "challenge:legacy_private_replay:rifle")
        attributes = graph.get(challenge).attributes
        self.assertEqual((attributes["origin"], attributes["plan"], attributes["commitment"], attributes["window"]), ("legacy_private_replay", None, None, None))

    def test_a_planned_challenge_is_its_public_identity_and_nothing_secret(self):
        from tests.test_challenge import follow, fresh, leaks, plan_for
        from fpsdet.challenge import ChallengeRegistry
        from fpsdet.challenge_plan import realize
        from fpsdet.pipeline import run_score

        key, secret = fresh()
        planned = plan_for(secret=secret)
        (plan,) = planned.plans
        case = next(case_to_dict(c) for c in run_score(follow(plan, n=16), DEMO.profile, challenges=ChallengeRegistry(planned.plans)))
        self.assertEqual(verify_graph(case), [])
        graph = graph_of(case)
        (challenge,) = node_ids(graph, "challenge")
        self.assertEqual(challenge, f"challenge:{plan.challenge_id}")
        self.assertEqual(graph.get(challenge).attributes, {
            "origin": "planned", "type": "occluded_motion_replay", "version": 1, "commitment": plan.commitment,
            "plan": plan.digest, "window": {"start_ms": plan.start_ms, "end_ms": plan.end_ms},
        })
        self.assertEqual(graph.out(challenge, "about"), [f"player:{plan.subject_id}"])
        self.assertEqual(graph.out(challenge, "occurred_in"), [f"match:{plan.match_id}"])
        material = realize(secret, plan, planned.budget).material
        self.assertEqual(leaks(json.dumps(case["evidence"]["graph"]), key, material), [])

    def test_external_records_their_groups_and_domains(self):
        from tests.test_external import FUSED

        graph = graph_of(FUSED["blasted"])
        records = node_ids(graph, "external_record")
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(graph.out(record, "provided_by"), ["provider_group:example-integrity"])
            self.assertEqual(graph.out(record, "uses_telemetry_domain"), ["telemetry_domain:endpoint_memory"])
            self.assertEqual(graph.out(record, "about"), ["player:blasted"])
        summary = FUSED["blasted"]["evidence"]["graph"]["summary"]
        kinds = {item["kind"]: item for item in summary["shared"]}
        self.assertEqual(kinds["provider_group"]["providers"], ["example-integrity"])
        self.assertEqual(len(kinds["provider_group"]["observations"]), 2)
        self.assertEqual(kinds["telemetry_domain"]["value"], "endpoint_memory")
        elite = graph_of(FUSED["elite-human"])
        self.assertEqual([graph_of(FUSED["elite-human"]).out(n.id, "supports") for n in elite.of_type("observation")], [[], []])  # context only
        for pid, case in FUSED.items():
            self.assertEqual(verify_graph(case), [], pid)

    def test_reports_are_not_in_the_graph(self):
        case = PLANTED["reported-streamer"]
        quiet = copy.deepcopy(case)
        quiet["reports"] = 0
        self.assertEqual(regraph(quiet)["evidence"]["graph"], case["evidence"]["graph"])
        self.assertEqual(sorted({node.type for node in graph_of(case).nodes}), ["case", "match", "player"])


class GraphTamperTest(unittest.TestCase):
    """verify_graph rebuilds the graph from the case and checks each relationship; it trusts no stored id."""

    def tampered(self, pid: str, edit, *, regraph_after: bool = False) -> list[str]:
        case = copy.deepcopy(PLANTED[pid])
        edit(case)
        if regraph_after:
            case["evidence"]["graph"]["digest"] = EvidenceGraph.from_dict(
                {k: v for k, v in case["evidence"]["graph"].items() if k not in ("summary", "digest")}).digest()
        return verify_graph(case)

    def test_edits_are_caught(self):
        def add_dependency(case):
            graph = case["evidence"]["graph"]
            graph["nodes"].append({"id": "observation:obs-nowhere", "type": "observation", "attributes": {"in_case": False}})
            voice = next(n["id"] for n in graph["nodes"] if n["attributes"].get("kind") == "voice")
            graph["edges"].append({"source": voice, "relation": "depends_on", "target": "observation:obs-nowhere", "attributes": {}})

        def drop_dependency(case):
            graph = case["evidence"]["graph"]
            graph["edges"] = [e for e in graph["edges"] if e["relation"] != "depends_on"]

        def edit_evidence_keep_id(case):
            observations(case, "voice")[0]["evidence"]["fast_lags_ms"] = [1]

        def edit_depends_on_keep_id(case):
            observations(case, "voice")[0]["depends_on"] = ["obs-somewhere-else"]

        def rename_partner(case):
            for edge in case["evidence"]["graph"]["edges"]:
                if edge["relation"] == "names_partner":
                    edge["target"] = "player:radar-friend"

        def edit_summary(case):
            case["evidence"]["graph"]["summary"]["independence"]["supporting"] = 9

        def drop_graph(case):
            del case["evidence"]["graph"]

        cases = {
            "an extra dependency": (add_dependency, True),
            "a dropped dependency": (drop_dependency, True),
            "an edited value under its old id": (edit_evidence_keep_id, False),
            "an edited depends_on under its old id": (edit_depends_on_keep_id, False),
            "a partner renamed": (rename_partner, True),
            "an edited summary": (edit_summary, False),
            "no graph": (drop_graph, False),
        }
        for name, (edit, regraph_after) in cases.items():
            with self.subTest(name):
                self.assertTrue(self.tampered("radar-friend", edit, regraph_after=regraph_after))

    def test_a_self_consistent_but_wrong_graph_is_caught(self):
        case = copy.deepcopy(PLANTED["clone-buyer"])
        stored = graph_of(case)
        forged = EvidenceGraph()
        for node in stored.nodes:
            forged.node(node.type, node.id.split(":", 1)[1], **node.attributes)
        for edge in stored.edges:
            if edge.relation != "names_partner":
                forged.edge(edge.source, edge.relation, edge.target)
        forged.edge(next(n.id for n in stored.of_type("observation")), "names_partner", forged.node("player", "someone-else"))
        case["evidence"]["graph"] = {**forged.to_dict(), "summary": describe(forged)}
        problems = verify_graph(case)
        self.assertTrue(any("does not match the case's evidence" in p for p in problems))
        self.assertTrue(any("does not name its partner" in p for p in problems))

    def test_a_cycle_or_a_dangling_edge_is_not_a_graph(self):
        for damage in ("cycle", "dangling"):
            case = copy.deepcopy(PLANTED["radar-friend"])
            graph = case["evidence"]["graph"]
            voice = next(n["id"] for n in graph["nodes"] if n["attributes"].get("kind") == "voice")
            hidden = next(n["id"] for n in graph["nodes"] if n["attributes"].get("in_case") is False)
            target, source = (voice, hidden) if damage == "cycle" else ("observation:gone", voice)
            graph["edges"].append({"source": source, "relation": "depends_on", "target": target, "attributes": {}})
            with self.subTest(damage):
                self.assertIn("not well formed", " ".join(verify_graph(case)))

    def test_a_dependency_on_a_stranger_is_incompatible(self):
        case = copy.deepcopy(PLANTED["radar-friend"])
        graph = graph_of(case)
        forged = EvidenceGraph()
        for node in graph.nodes:
            forged.node(node.type, node.id.split(":", 1)[1], **node.attributes)
        for edge in graph.edges:
            if not (edge.relation == "about" and edge.source.startswith("observation:") and edge.target == "player:wall-eye"):
                forged.edge(edge.source, edge.relation, edge.target)
        hidden = next(n.id for n in forged.of_type("observation") if n.attributes.get("in_case") is False)
        forged.edge(hidden, "about", forged.node("player", "stranger"))
        from fpsdet.graph import _relationship_problems

        self.assertIn("not its partner's", " ".join(_relationship_problems(case, forged)))

    def test_across_cases_the_dependency_must_exist_on_the_partners_case(self):
        cases = copy.deepcopy(PLANTED)
        self.assertEqual(verify_graphs(cases.values()), [])
        cases["wall-eye"]["evidence"]["observations"] = []
        self.assertTrue(verify_graphs(cases.values()))
        del cases["wall-eye"]
        self.assertTrue(verify_graphs(cases.values()))


class GraphBoundaryTest(unittest.TestCase):
    """Provider data fills values. fpsdet decides every node type, relation and key."""

    def test_external_metadata_cannot_shape_the_graph(self):
        from tests.test_external import native, scored, write_lines
        from fpsdet.external import read_external

        hostile = native(subject_id="adrenaline", kind="depends_on observation:obs-x", provider_record_id="case:adrenaline",
                         metadata={"relation": "depends_on", "node": "observation:obs-1", "type": "challenge"})
        with tempfile.TemporaryDirectory() as folder:
            loaded = read_external([(write_lines(Path(folder), "r.ndjson", [hostile]), None)])
        case = scored(loaded)["adrenaline"]
        self.assertEqual(verify_graph(case), [])
        graph = graph_of(case)
        self.assertEqual(sorted({node.type for node in graph.nodes}), ["case", "external_record", "match", "observation", "player", "provider_group", "telemetry_domain"])
        self.assertEqual(len(graph.of_type("observation")), 1)
        self.assertNotIn("obs-x", json.dumps(case["evidence"]["graph"]))
        self.assertNotIn("depends_on observation", json.dumps(case["evidence"]["graph"]))


class IndependenceReadinessTest(unittest.TestCase):
    """Why the graph matters before any rule uses it: evidence that looks separate and is not."""

    def records(self, rows, events=None):
        from tests.test_external import scored, write_lines
        from fpsdet.external import read_external

        with tempfile.TemporaryDirectory() as folder:
            loaded = read_external([(write_lines(Path(folder), "r.ndjson", rows), None)])
        return scored(loaded, events=events)

    def test_independent_looking_names_from_one_provider_group(self):
        from tests.test_external import native

        case = self.records([native(subject_id="adrenaline", provider="vendor-a", provider_group="parent-company-x"),
                             native(subject_id="adrenaline", provider="vendor-b", provider_group="parent-company-x")])["adrenaline"]
        summary = case["evidence"]["graph"]["summary"]
        (group,) = [item for item in summary["shared"] if item["kind"] == "provider_group"]
        self.assertEqual((group["value"], group["providers"]), ("parent-company-x", ["vendor-a", "vendor-b"]))
        self.assertEqual(summary["independence"]["provider_groups"], ["parent-company-x"])
        self.assertEqual(case["decision"], "watch")

    def test_different_providers_on_one_telemetry_domain(self):
        from tests.test_external import native

        case = self.records([native(subject_id="adrenaline", provider="vendor-a", telemetry_domain="endpoint_memory"),
                             native(subject_id="adrenaline", provider="vendor-b", telemetry_domain="endpoint_memory")])["adrenaline"]
        summary = case["evidence"]["graph"]["summary"]
        self.assertEqual(summary["independence"]["provider_groups"], ["vendor-a", "vendor-b"])
        (domain,) = [item for item in summary["shared"] if item["kind"] == "telemetry_domain"]
        self.assertEqual(domain["value"], "endpoint_memory")
        self.assertEqual(case["decision"], "watch")  # two names, one domain, still a watch

    def test_a_server_challenge_and_an_endpoint_signal_are_kept_apart(self):
        from tests.test_challenge import follow, plan_for
        from tests.test_external import native, write_lines
        from fpsdet.challenge import ChallengeRegistry
        from fpsdet.external import read_external
        from fpsdet.models import GameProfile
        from fpsdet.pipeline import run_score

        planned = plan_for()
        (plan,) = planned.plans
        with tempfile.TemporaryDirectory() as folder:
            loaded = read_external([(write_lines(Path(folder), "r.ndjson", [native(subject_id="x", match_id=plan.match_id, telemetry_domain="endpoint_memory")]), None)])
        case = next(case_to_dict(c) for c in run_score(follow(plan, n=16), GameProfile(game_id="g"), challenges=ChallengeRegistry(planned.plans), external=loaded))
        self.assertEqual(verify_graph(case), [])
        summary = case["evidence"]["graph"]["summary"]
        domains = {row["family"]: row["telemetry_domain"] for row in summary["observations"].values()}
        self.assertEqual(domains, {"challenge": "server_challenge", "external": "endpoint_memory"})
        self.assertEqual(summary["shared"], [])
        self.assertEqual(summary["independence"]["telemetry_domains"], ["endpoint_memory", "server_challenge"])
        self.assertEqual((case["decision"], case["evidence"]["fusion"]["rule"]), ("review", "C"))  # the challenge's review; nothing escalated

    def test_two_observations_resting_on_one_hidden_mover_share_a_dependency(self):
        graph = EvidenceGraph()
        case = graph.node("case", "mate", decision="watch")
        graph.edge(case, "about", graph.node("player", "mate"))
        hidden = graph.node("observation", "obs-hidden", in_case=False)
        graph.edge(hidden, "about", graph.node("player", "cheater"))
        for name in ("obs-voice-1", "obs-voice-2"):
            node = graph.node("observation", name, in_case=True, source="fpsdet", family="relationship", kind="voice", role="watch")
            graph.edge(node, "depends_on", hidden)
            graph.edge(node, "names_partner", "player:cheater")
            graph.edge(node, "supports", case)
        shared = {item["kind"]: item for item in describe(graph)["shared"]}
        self.assertEqual(shared["dependency"]["value"], "observation:obs-hidden")
        self.assertEqual(shared["dependency"]["observations"], ["observation:obs-voice-1", "observation:obs-voice-2"])
        self.assertEqual(shared["partner"]["value"], "player:cheater")


if __name__ == "__main__":
    unittest.main()
