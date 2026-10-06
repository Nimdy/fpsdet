"""The evidence graph: what each piece of evidence is, where it came from, and what it depends on.

CurrentRelationshipsTest was written before the graph (P6, phase G0) to pin every relationship the
evidence already carries, and where each one lives, so the graph can be checked against them.
"""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
