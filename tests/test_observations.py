"""Structured evidence: the observation model, and the scorer's findings recorded through it."""

from __future__ import annotations

import json
import unittest

from fpsdet.evidence import (
    FAMILIES,
    KINDS,
    ROLES,
    Observation,
    canonical_json,
    evidence_block,
    implied_decision,
)
from fpsdet.models import CHECKS


def accuracy(**changes) -> Observation:
    fields = dict(
        family="human_baseline",
        kind="accuracy",
        role="past_human",
        subject_id="p-1",
        key="rifle",
        match_ids=("m1", "m2"),
        evidence={"metric": "accuracy", "successes": 185, "trials": 200, "bound": {"method": "wilson_lower", "value": 0.89}},
        context={"line": "rifle accuracy lower bound 89% is past the best measured elite human (37%)"},
    )
    fields.update(changes)
    return Observation(**fields)


def block(*observations: Observation, compared=(("accuracy", "rifle"),)) -> dict:
    return evidence_block(observations, compared)


class ObservationModelTest(unittest.TestCase):
    def test_the_same_material_evidence_has_the_same_id(self):
        self.assertEqual(accuracy().observation_id, accuracy().observation_id)
        self.assertRegex(accuracy().observation_id, r"^obs-[0-9a-f]{24}$")
        # Key order in the payload is not material.
        reordered = accuracy(evidence={"trials": 200, "bound": {"value": 0.89, "method": "wilson_lower"}, "successes": 185, "metric": "accuracy"})
        self.assertEqual(reordered.observation_id, accuracy().observation_id)

    def test_a_material_change_moves_the_id(self):
        base = accuracy().observation_id
        moved = [
            accuracy(evidence={"metric": "accuracy", "successes": 186, "trials": 200, "bound": {"method": "wilson_lower", "value": 0.89}}),
            accuracy(subject_id="p-2"),
            accuracy(key="smg"),
            accuracy(match_ids=("m1",)),
            accuracy(depends_on=("obs-000000000000000000000000",)),
            accuracy(kind="headshot_rate"),
        ]
        ids = [obs.observation_id for obs in moved]
        self.assertNotIn(base, ids)
        self.assertEqual(len(set(ids)), len(ids))

    def test_context_is_not_material(self):
        reworded = accuracy(context={"line": "said another way", "note": "anything"})
        self.assertEqual(reworded.observation_id, accuracy().observation_id)
        self.assertNotEqual(reworded.to_dict(), accuracy().to_dict())

    def test_serialization_is_deterministic_data(self):
        first, second = accuracy().to_dict(), accuracy(evidence=dict(reversed(list(accuracy().evidence.items())))).to_dict()
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(
            sorted(first),
            ["context", "depends_on", "evidence", "family", "key", "kind", "match_ids", "observation_id", "role", "source", "subject_id"],
        )
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        wrapped = block(accuracy(), compared=[("accuracy", "smg"), ("accuracy", "rifle"), ("accuracy", "rifle")])
        self.assertEqual(wrapped["version"], 1)
        self.assertEqual(wrapped["eligibility"]["compared"], [{"key": "rifle", "metric": "accuracy"}, {"key": "smg", "metric": "accuracy"}])
        self.assertEqual(canonical_json(wrapped), canonical_json(block(accuracy(), compared=[("accuracy", "rifle"), ("accuracy", "smg")])))

    def test_only_known_families_and_their_kinds(self):
        with self.assertRaises(ValueError):
            accuracy(family="challenge")
        with self.assertRaises(ValueError):
            accuracy(family="information")  # accuracy is a human-baseline number
        with self.assertRaises(ValueError):
            accuracy(kind="aimbot_score")
        self.assertEqual({family for family, _, _ in KINDS.values()}, set(FAMILIES))

    def test_only_the_role_the_scorer_gives(self):
        with self.assertRaises(ValueError):
            accuracy(role="ban")
        with self.assertRaises(ValueError):
            accuracy(role="review")  # one accuracy past the humans is a watch, not a review
        self.assertEqual({role for _, _, role in KINDS.values()}, set(ROLES))

    def test_only_plain_data_enters_evidence(self):
        for bad in ({"seen": {"a", "b"}}, {"when": object()}, {"raw": b"bytes"}, {1: "int key"}):
            with self.assertRaises(TypeError):
                accuracy(evidence=bad)
        with self.assertRaises(TypeError):
            accuracy(match_ids={"m1", "m2"})  # a set has no fixed order
        with self.assertRaises(TypeError):
            accuracy(match_ids="m1")
        last_bit = accuracy(evidence={**accuracy().evidence, "observed": 0.1 + 0.2}).observation_id
        self.assertEqual(last_bit, accuracy(evidence={**accuracy().evidence, "observed": 0.3}).observation_id)
        odd = accuracy(evidence={"metric": "accuracy", "observed": float("inf"), "spread": float("nan")})
        self.assertEqual((odd.evidence["observed"], odd.evidence["spread"]), ("Infinity", "NaN"))
        json.dumps(odd.to_dict(), allow_nan=False)

    def test_an_observation_cannot_carry_an_action(self):
        for bad in ({"automated_action": "ban"}, {"cohort": {"recommended_action": "ban"}}, {"steps": [{"action": "kick"}]}):
            with self.assertRaises(ValueError):
                accuracy(evidence=bad)
        with self.assertRaises(ValueError):
            accuracy(context={"action": "ban"})
        self.assertNotIn("automated_action", accuracy().to_dict())


class ImpliedDecisionTest(unittest.TestCase):
    """The rules of score.decide, read from roles. The scorer does not call this."""

    def obs(self, kind: str, metric: str | None = None, key: str = "rifle") -> Observation:
        family, _, role = KINDS[kind]
        evidence = {"metric": metric or kind}
        return Observation(family=family, kind=kind, role=role, subject_id="p", key=key, evidence=evidence)

    def test_each_rule(self):
        cases = [
            ([self.obs("speed")], "review"),
            ([self.obs("accuracy"), self.obs("headshot_rate")], "review"),
            ([self.obs("accuracy"), self.obs("account_jump")], "review"),
            ([self.obs("accuracy"), self.obs("view_snaps")], "review"),
            ([self.obs("accuracy")], "watch"),
            ([self.obs("accuracy"), self.obs("accuracy", key="smg")], "watch"),  # one metric on two guns counts once
            ([self.obs("extra", "kills_per_min"), self.obs("extra", "damage_per_min")], "review"),  # two declared metrics are two
            ([self.obs("account_jump")], "watch"),
            ([self.obs("rank_tail", "accuracy")], "watch"),
            ([self.obs("leftover")], "watch"),
            ([self.obs("view_snaps"), self.obs("acquire_timing")], "watch"),
            ([self.obs("acquire_timing"), self.obs("acquire_timing", key="smg")], "clean"),  # one family twice is one
            ([self.obs("view_snaps")], "clean"),
        ]
        for observations, expected in cases:
            kinds = [obs.kind for obs in observations]
            self.assertEqual(implied_decision(block(*observations)), expected, kinds)

    def test_no_finding_is_clean_only_after_a_comparison(self):
        self.assertEqual(implied_decision(block()), "clean")
        self.assertEqual(implied_decision(block(compared=())), "insufficient_data")
        self.assertEqual(implied_decision(block(self.obs("leftover"), compared=())), "watch")

    def test_every_case_check_has_a_kind(self):
        self.assertEqual({check for _, check, _ in KINDS.values()}, set(CHECKS))


if __name__ == "__main__":
    unittest.main()
