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
from fpsdet.ai_triage import triage_case
from fpsdet.baseline import build_cohorts
from fpsdet.models import CHECKS, Event, ExtraMetric, GameProfile
from fpsdet.ops import ops_payload
from fpsdet.persist import case_to_dict
from fpsdet.score import assess_player
from fpsdet.summarize import summarize
from fpsdet.synthetic import build_demo
from fpsdet.week import build_week


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


def linked(case) -> list[str]:
    """What does not line up between a case and its evidence. Empty when everything does."""
    block = case_to_dict(case)["evidence"]
    observations = block["observations"]
    problems = []
    implied = implied_decision(block)
    if implied != case.decision:
        problems.append(f"{case.player_id}: decision {case.decision}, evidence implies {implied}")
    if {KINDS[obs["kind"]][1] for obs in observations} != set(case.checks):
        problems.append(f"{case.player_id}: checks {case.checks}, evidence fires {sorted({KINDS[o['kind']][1] for o in observations})}")
    printed = sorted(obs["context"]["line"] for obs in observations if obs["context"]["printed_in"] == "reasons")
    if printed != sorted(case.reasons):
        problems.append(f"{case.player_id}: a reason has no observation, or an observation no reason")
    for obs in observations:
        if obs["context"]["printed_in"] == "observations" and obs["context"]["line"] not in case.observations:
            problems.append(f"{case.player_id}: {obs['kind']} line is not among the context lines")
    ids = [obs["observation_id"] for obs in observations]
    if len(ids) != len(set(ids)):
        problems.append(f"{case.player_id}: two observations share an id")
    if any(obs["subject_id"] != case.player_id for obs in observations):
        problems.append(f"{case.player_id}: an observation is about someone else")
    return problems


class RecordedFindingsTest(unittest.TestCase):
    """The scorer's findings, recorded as observations beside the sentences. Nothing else moves."""

    @classmethod
    def setUpClass(cls):
        cls.demo = build_demo()
        cls.week = build_week()

    def test_every_planted_decision_is_explained_by_its_evidence(self):
        problems = [problem for case in self.demo.cases for problem in linked(case)]
        self.assertEqual(problems, [])

    def test_every_weekly_and_nightly_decision_is_explained(self):
        everything = self.week.cases + [case for night in self.week.nightly for case in night]
        problems = [problem for case in everything for problem in linked(case)]
        self.assertEqual(problems, [], problems[:5])
        self.assertEqual(len(everything), 400 + sum(len(night) for night in self.week.nightly))

    def test_a_watch_with_no_reason_line_is_still_explained(self):
        quiet = [case for case in self.demo.cases + self.week.cases if case.decision == "watch" and not case.reasons]
        self.assertTrue(quiet)
        for case in quiet:
            block = case_to_dict(case)["evidence"]
            self.assertEqual(implied_decision(block), "watch", case.player_id)
            self.assertTrue(any(obs["role"] in ("watch", "past_human", "account_change") for obs in block["observations"]), case.player_id)

    def test_a_rank_tail_keeps_the_numbers_it_was_judged_on(self):
        case = next(case for case in self.demo.cases if case.player_id == "rank-outlier")
        (tail,) = case_to_dict(case)["evidence"]["observations"]
        self.assertEqual((tail["kind"], tail["role"], tail["key"], tail["family"]), ("rank_tail", "watch", "rifle", "human_baseline"))
        numbers = tail["evidence"]
        self.assertEqual((numbers["metric"], numbers["trials"], numbers["past_rank"], numbers["past_human"]), ("accuracy", 200, True, False))
        # Past the rank's p95, inside the best human measured: that is what makes it a watch and not a review.
        self.assertGreater(numbers["bound"]["value"], numbers["rank"]["p95"])
        self.assertLess(numbers["bound"]["value"], numbers["ceiling"]["max"])
        self.assertEqual((numbers["rank"]["band"], numbers["ceiling"]["band"]), ("average", "elite"))
        self.assertEqual(numbers["successes"] / numbers["trials"], numbers["observed"])

    def test_the_teammate_watch_points_at_the_hidden_mover_it_followed(self):
        by_id = {case.player_id: case for case in self.demo.cases}
        (voice,) = case_to_dict(by_id["radar-friend"])["evidence"]["observations"]
        hidden = [obs["observation_id"] for obs in case_to_dict(by_id["wall-eye"])["evidence"]["observations"] if obs["kind"] == "hidden"]
        self.assertEqual((voice["kind"], voice["evidence"]["partner"], voice["depends_on"]), ("voice", "wall-eye", hidden))
        self.assertTrue(all(lag < voice["evidence"]["thresholds"]["voice_ms"] for lag in voice["evidence"]["fast_lags_ms"]))

    def test_the_shared_leftover_keeps_its_correlation_and_bar(self):
        by_id = {case.player_id: case for case in self.demo.cases}
        (buyer,) = [obs for obs in case_to_dict(by_id["clone-buyer"])["evidence"]["observations"] if obs["kind"] == "leftover"]
        numbers = buyer["evidence"]
        self.assertEqual((numbers["partner"], numbers["partner_in_review"], buyer["role"]), ("clone-source", True, "watch"))
        self.assertGreaterEqual(numbers["r"], numbers["thresholds"]["min_r"])
        self.assertGreaterEqual(numbers["fisher_z"], numbers["thresholds"]["min_z"])
        self.assertGreaterEqual(numbers["points"], numbers["thresholds"]["min_points"])
        (source,) = [obs for obs in case_to_dict(by_id["clone-source"])["evidence"]["observations"] if obs["kind"] == "leftover"]
        self.assertEqual(source["context"]["printed_in"], "observations")  # already a review: a context line, no new reason

    def test_scoring_twice_records_the_same_evidence(self):
        again = {case.player_id: case for case in build_demo().cases}
        for case in self.demo.cases:
            self.assertEqual(canonical_json(case_to_dict(case)["evidence"]), canonical_json(case_to_dict(again[case.player_id])["evidence"]))

    def test_reports_change_no_evidence(self):
        records = {record.player_id: record for record in summarize(self.demo.events, self.demo.profile)}
        cohort = build_cohorts(summarize(self.demo.population, self.demo.profile), self.demo.profile)
        for player_id in ("reported-streamer", "rank-outlier", "small-sample", "rage"):
            quiet = assess_player(records[player_id], cohort, self.demo.profile, [], 0)
            loud = assess_player(records[player_id], cohort, self.demo.profile, [], 500)
            self.assertEqual(case_to_dict(quiet)["evidence"], case_to_dict(loud)["evidence"], player_id)
            self.assertEqual(quiet.decision, loud.decision)
        streamer = next(case for case in self.demo.cases if case.player_id == "reported-streamer")
        self.assertEqual((streamer.reports, case_to_dict(streamer)["evidence"]["observations"]), (25, []))
        self.assertEqual(implied_decision(case_to_dict(streamer)["evidence"]), "clean")

    def test_the_views_and_the_ai_brief_read_what_they_read_before(self):
        payload = ops_payload(profile=self.demo.profile, cases=self.demo.cases)
        self.assertFalse(any("evidence" in row.get("case", {}) for row in payload["rows"]))
        sent = []
        transport = lambda body: sent.append(body) or "brief"
        case = case_to_dict(next(c for c in self.demo.cases if c.player_id == "radar-friend"))
        triage_case(case, transport, known_ids=["radar-friend", "wall-eye"])
        triage_case({key: value for key, value in case.items() if key != "evidence"}, transport, known_ids=["radar-friend", "wall-eye"])
        self.assertEqual(sent[0], sent[1])
        self.assertNotIn("wall-eye", json.dumps(sent[0]))


def _population_event(player_id: str, index: int, **values) -> Event:
    base = dict(
        game_id="g", match_id=f"m-{player_id}", player_id=player_id, t_ms=index * 150, event_type="shot",
        skill_band="average", weapon_class="rifle", weapon_id="ak", spray_index=index % 10,
    )
    base.update(values)
    extras = {"dmg": base.pop("dmg"), "calm": base.pop("calm")}
    return Event(**base, extras=extras)


class EveryKindTest(unittest.TestCase):
    """The kinds the planted demo and the week never fire: geometry, declared numbers, snaps, timing, learned recoil."""

    def test_one_player_past_everything_records_each_kind_with_its_numbers(self):
        import random

        rng = random.Random(4)
        profile = GameProfile(
            game_id="g",
            extra_metrics=[
                ExtraMetric("dmg", "dmg", "primary", "high", min_samples=10, group_by=("weapon_class",)),
                ExtraMetric("calm", "calm", "supporting", "high", min_samples=10, group_by=("weapon_class",)),
            ],
        )
        humans = []
        for player in range(40):
            pid = f"h-{player:02d}"
            for index in range(60):
                hit = rng.random() < 0.2
                humans.append(_population_event(
                    pid, index, hit=hit, hitbox="head" if hit and rng.random() < 0.3 else ("limbs" if hit else None),
                    distance_m=rng.uniform(20, 40), through_geometry=rng.random() < 0.05, view_delta_deg=rng.uniform(5, 30),
                    acquire_ms=rng.uniform(300, 500), recoil_pitch_deg=rng.uniform(1.0, 1.5), dmg=rng.uniform(100, 150), calm=rng.uniform(0.3, 0.5),
                ))
        cheat = [
            _population_event(
                "x", index, hit=index % 5 == 0, hitbox="limbs" if index % 5 == 0 else None, distance_m=30.0,
                through_geometry=index % 5 != 0, view_delta_deg=150.0, acquire_ms=50.0 + index % 3, recoil_pitch_deg=0.05,
                dmg=400.0, calm=5.0,
            )
            for index in range(60)
        ]
        cohort = build_cohorts(summarize(humans, profile), profile)
        (record,) = summarize(cheat, profile)
        case = assess_player(record, cohort, profile)
        self.assertEqual(linked(case), [])
        block = case_to_dict(case)["evidence"]
        kinds = {obs["kind"]: obs for obs in block["observations"]}
        self.assertEqual(
            set(kinds),
            {"recoil_learned", "geometry_rate", "extra", "supporting_extra", "view_snaps", "acquire_timing"},
        )
        self.assertEqual(case.decision, "review")
        geometry = kinds["geometry_rate"]["evidence"]
        self.assertEqual((geometry["successes"], geometry["trials"]), (48, 60))
        self.assertGreater(geometry["bound"]["value"], geometry["ceiling"]["max"])
        self.assertEqual(geometry["ceiling"]["players"], 40)
        learned = kinds["recoil_learned"]["evidence"]
        self.assertLess(learned["bound"]["value"], min(learned["ceiling"]["min"], learned["learned_floor"]))
        self.assertEqual(kinds["recoil_learned"]["key"], "ak|")
        self.assertEqual((kinds["extra"]["evidence"]["metric"], kinds["supporting_extra"]["evidence"]["metric"]), ("dmg", "calm"))
        self.assertLess(kinds["acquire_timing"]["evidence"]["median"]["observed"], kinds["acquire_timing"]["evidence"]["median"]["ceiling"]["min"])
        compared = {(row["metric"], row["key"]) for row in block["eligibility"]["compared"]}
        self.assertTrue({("accuracy", "rifle"), ("geometry_rate", "rifle"), ("recoil", "ak|"), ("dmg", "rifle")} <= compared)
        # Without the learned recoil, the two numbers past every human make the review on their own.
        rest = {**block, "observations": [obs for obs in block["observations"] if obs["kind"] != "recoil_learned"]}
        self.assertEqual(implied_decision(rest), "review")


if __name__ == "__main__":
    unittest.main()
