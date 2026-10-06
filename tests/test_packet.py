"""History provenance, and the evidence packet: one digest over the material evidence and what produced it."""

from __future__ import annotations

import copy
import json
import unittest

from fpsdet.baseline import build_cohorts
from fpsdet.evidence import Observation
from fpsdet.models import HistoryWindow
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.provenance import PACKET_RECIPE, PACKET_V1, history_digest, packet_block, verify_packet
from fpsdet.score import assess_player, history_for
from fpsdet.signals import evidence_seal
from fpsdet.summarize import summarize
from fpsdet.synthetic import build_demo

# A fixed set of history windows, and a fixed serialized case. CI checks both digests on Python 3.11 and 3.12.
PINNED_HISTORY_DIGEST = "sha256:ba3a28f8a5c7176e8c51e3b05edcf9682af70bbc51d707ddbf04d98cd0acf89d"
PINNED_PACKET_DIGEST = "sha256:97706dae36ad8c624e6a8e40c1e4c912989c19b32287e9752e92e9acb99894d7"


def windows(*rows) -> list[HistoryWindow]:
    return [HistoryWindow(*row) for row in rows]


ROWS = (("p", "rifle", "average", 200, 8), ("p", "smg", "average", 120, 20), ("q", "rifle", "average", 300, 60))


class HistoryProvenanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = build_demo()
        cls.cohort = build_cohorts(summarize(cls.demo.population, cls.demo.profile), cls.demo.profile)
        cls.records = {record.player_id: record for record in summarize(cls.demo.events, cls.demo.profile)}

    def test_the_same_windows_in_any_order_have_one_digest(self):
        digest = history_digest(windows(*ROWS))
        self.assertEqual(history_digest(windows(*reversed(ROWS))), digest)
        self.assertEqual(digest, PINNED_HISTORY_DIGEST)

    def test_each_field_of_a_window_counts(self):
        base = history_digest(windows(*ROWS))
        for changed in (("p", "rifle", "average", 201, 8), ("p", "rifle", "average", 200, 9), ("z", "rifle", "average", 200, 8),
                        ("p", "dmr", "average", 200, 8), ("p", "rifle", "elite", 200, 8)):
            self.assertNotEqual(history_digest(windows(changed, *ROWS[1:])), base, changed)

    def test_a_repeated_window_counts_twice_as_the_scorer_counts_it(self):
        once, twice = windows(("account-changed", "rifle", "average", 200, 8)), windows(*[("account-changed", "rifle", "average", 200, 8)] * 2)
        self.assertNotEqual(history_digest(once), history_digest(twice))
        record = self.records["account-changed"]
        sums = []
        for history in (once, twice):
            case = assess_player(record, self.cohort, self.demo.profile, history)
            jump = next(obs for obs in case.evidence if obs.kind == "account_jump")
            sums.append(jump.evidence["history"]["shots"])
        self.assertEqual(sums, [200, 400])  # the account check adds both windows up

    def test_the_scope_is_what_the_scorer_can_read_for_the_subject(self):
        record = self.records["account-changed"]
        history = windows(("account-changed", "rifle", "average", 200, 8), ("account-changed", "dmr", "average", 90, 50),
                          ("account-changed", "rifle", "elite", 90, 50), ("rage", "rifle", "average", 200, 190))
        # Only its own rows, on a weapon key and band it used: the others cannot change its case.
        self.assertEqual(history_for(record, history), history[:1])
        base = run_score(self.demo.events, self.demo.profile, self.cohort, history)
        other = run_score(self.demo.events, self.demo.profile, self.cohort, history[:1] + windows(("rage", "rifle", "average", 10, 1)))
        digests = lambda cases: {case.player_id: case.provenance.history.digest for case in cases}
        before, after = digests(base), digests(other)
        self.assertEqual(before["account-changed"], after["account-changed"])
        self.assertNotEqual(before["rage"], after["rage"])
        self.assertEqual({pid for pid in before if before[pid] != after[pid]}, {"rage"})

    def test_no_history_and_an_empty_history_are_the_same_input(self):
        # The scorer reads both as no windows (history or []), so both are mode none.
        for history in (None, []):
            case = run_score(self.demo.events, self.demo.profile, self.cohort, history)[0]
            self.assertEqual(case_to_dict(case)["evidence"]["provenance"]["history"], {"mode": "none"})
        case = next(c for c in run_score(self.demo.events, self.demo.profile, self.cohort, windows(("someone-else", "rifle", "average", 1, 1))) if c.player_id == "rage")
        # A history was given, and none of it is this player's: said, not hidden.
        block = case_to_dict(case)["evidence"]["provenance"]["history"]
        self.assertEqual((block["mode"], block["windows"]), ("external", 0))

    def test_the_account_jump_case_carries_the_history_it_read(self):
        case = next(c for c in self.demo.cases if c.player_id == "account-changed")
        block = case_to_dict(case)["evidence"]["provenance"]["history"]
        self.assertEqual((block["mode"], block["windows"]), ("external", 1))
        self.assertEqual(block["digest"], history_digest(windows(("account-changed", "rifle", "average", 200, 8))))
        self.assertIn("account_jump", case.checks)

    def test_history_provenance_leaves_observation_ids_alone(self):
        from fpsdet.provenance import CaseProvenance, history_provenance

        case = next(c for c in self.demo.cases if c.player_id == "account-changed")
        ids = [obs["observation_id"] for obs in case_to_dict(case)["evidence"]["observations"]]
        kept = case.provenance
        case.provenance = CaseProvenance(kept.run, kept.inputs, history_provenance(None))
        try:
            self.assertEqual([obs["observation_id"] for obs in case_to_dict(case)["evidence"]["observations"]], ids)
        finally:
            case.provenance = kept


def fixed_case() -> dict:
    """A small serialized case with fixed provenance, for a digest that does not depend on this checkout's code."""
    obs = Observation(family="physics", kind="speed", role="review", subject_id="p-1", match_ids=("m1",),
                      evidence={"longest_run": 30, "thresholds": {"min_run": 25}}, context={"line": "ran"})
    digest = lambda c: "sha256:" + c * 64
    case = {
        "player_id": "p-1", "game_id": "g", "decision": "review", "reasons": ["ran"], "seal": "s", "ai_brief": "", "queue_rank": 0, "reports": 0,
        "evidence": {
            "version": 1,
            "observations": [obs.to_dict()],
            "eligibility": {"compared": [{"metric": "accuracy", "key": "rifle"}]},
            "provenance": {
                "version": 1,
                "profile": {"recipe": "fpsdet.profile/1", "digest": digest("1")},
                "detector": {"recipe": "fpsdet.detector/1", "digest": digest("2"), "modules": ["fpsdet", "fpsdet.score"]},
                "cohort": {"mode": "external", "recipe": "fpsdet.cohort/1", "digest": digest("3"), "stored_digest": "matched",
                           "integrity": {"recipe": "fpsdet.cohort-integrity/1", "status": "ok", "digest": digest("4")}},
                "inputs": {"recipe": "fpsdet.player-events/1", "digest": digest("5"), "events": 30, "matches": 1},
                "history": {"mode": "external", "recipe": "fpsdet.history/1", "digest": digest("6"), "windows": 2},
            },
        },
    }
    case["evidence"]["packet"] = packet_block(case, PACKET_V1)  # a pinned packet/1 digest
    return case


class PacketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = build_demo()
        cls.by_id = {case.player_id: case for case in cls.demo.cases}

    def serialized(self, player_id: str = "radar-friend") -> dict:
        return case_to_dict(self.by_id[player_id])

    def test_every_planted_packet_is_complete_and_verifies(self):
        for case in self.demo.cases:
            row = case_to_dict(case)
            self.assertEqual(row["evidence"]["packet"]["status"], "complete", case.player_id)
            self.assertEqual(verify_packet(row), [], case.player_id)
        self.assertEqual(self.serialized()["evidence"]["packet"], self.serialized()["evidence"]["packet"])
        self.assertEqual(self.serialized()["evidence"]["packet"]["recipe"], PACKET_RECIPE)

    def test_the_digest_is_the_same_on_every_python(self):
        case = fixed_case()
        self.assertEqual(verify_packet(case), [])
        self.assertEqual(case["evidence"]["packet"]["digest"], PINNED_PACKET_DIGEST)

    def assertCaught(self, edit, *, why: str):
        case = self.serialized("account-changed")
        before = case["evidence"]["packet"]["digest"]
        edit(case)
        self.assertNotEqual(verify_packet(case), [], why)
        self.assertNotEqual(packet_block(case).get("digest"), before, why)

    def test_material_edits_are_caught(self):
        def provenance(part, key, value):
            return lambda case: case["evidence"]["provenance"][part].__setitem__(key, value)

        jump = lambda case: next(obs for obs in case["evidence"]["observations"] if obs["kind"] == "account_jump")
        other = "sha256:" + "f" * 64
        edits = {
            "decision": lambda case: case.__setitem__("decision", "clean"),
            "eligibility": lambda case: case["evidence"]["eligibility"]["compared"].pop(),
            "subject": lambda case: case.__setitem__("player_id", "someone-else"),
            "game": lambda case: case.__setitem__("game_id", "another-game"),
            "detector digest": provenance("detector", "digest", other),
            "profile digest": provenance("profile", "digest", other),
            "cohort digest": provenance("cohort", "digest", other),
            "cohort mode": provenance("cohort", "mode", "in_file"),
            "integrity warning": lambda case: case["evidence"]["provenance"]["cohort"]["integrity"].update(status="poison_risk", digest=other),
            "input digest": provenance("inputs", "digest", other),
            "history digest": provenance("history", "digest", other),
        }
        for why, edit in edits.items():
            self.assertCaught(edit, why=why)
        # An observation edited in place: its own id no longer matches its contents.
        case = self.serialized("account-changed")
        jump(case)["evidence"]["gap"] = 0.5
        self.assertTrue(any("does not match its own contents" in problem for problem in verify_packet(case)))

    def test_an_observation_edited_with_a_fresh_id_still_moves_the_packet(self):
        case = self.serialized("account-changed")
        obs = next(obs for obs in case["evidence"]["observations"] if obs["kind"] == "account_jump")
        fields = {key: obs[key] for key in ("family", "kind", "role", "subject_id", "key", "evidence", "source")}
        fields["evidence"] = {**obs["evidence"], "gap": 0.5}
        obs.update(Observation(**fields, match_ids=tuple(obs["match_ids"]), depends_on=tuple(obs["depends_on"])).to_dict())
        problems = verify_packet(case)
        self.assertEqual([p for p in problems if "observation" in p and not p.startswith("graph:")], [])  # the id now matches the edit
        self.assertIn("the packet digest does not match the evidence and provenance it covers", problems)
        # Since packet/3, the graph notices too: the observation no longer matches the graph's node for it.
        self.assertTrue(any(p.startswith("graph:") for p in problems))

    def test_a_role_that_is_not_the_scorers_is_refused(self):
        case = self.serialized("rank-outlier")
        case["evidence"]["observations"][0]["role"] = "review"
        self.assertTrue(verify_packet(case))

    def test_wording_briefs_reports_and_queue_state_are_not_evidence(self):
        case = self.serialized("account-changed")
        digest = case["evidence"]["packet"]["digest"]
        case["reasons"] = ["reworded for a person"]
        case["evidence"]["observations"][0]["context"] = {"line": "reworded", "printed_in": "reasons"}
        case["ai_brief"] = "a different brief"
        case["queue_rank"] = 7
        case["reports"] = 99
        case["party_note"] = "another note"
        self.assertEqual(verify_packet(case), [])
        self.assertEqual(packet_block(case)["digest"], digest)

    def test_the_seal_is_unchanged_and_not_part_of_the_packet(self):
        case = self.by_id["account-changed"]
        self.assertEqual(evidence_seal(case), case.seal)
        row = case_to_dict(case)
        row["seal"] = "edited"
        self.assertEqual(verify_packet(row), [])

    def test_a_case_without_full_provenance_has_no_digest(self):
        record = summarize(self.demo.events, self.demo.profile)[0]
        cohort = build_cohorts(summarize(self.demo.population, self.demo.profile), self.demo.profile)
        row = case_to_dict(assess_player(record, cohort, self.demo.profile))
        self.assertEqual(row["evidence"]["packet"], {"recipe": PACKET_RECIPE, "status": "incomplete", "missing": ["provenance"]})
        self.assertNotIn("digest", row["evidence"]["packet"])
        self.assertEqual(verify_packet(row), [])
        row["evidence"]["packet"] = {"recipe": PACKET_RECIPE, "status": "complete", "digest": "sha256:" + "0" * 64}
        self.assertTrue(verify_packet(row))

    def test_a_case_without_a_packet_does_not_verify(self):
        row = self.serialized()
        del row["evidence"]["packet"]
        self.assertEqual(verify_packet(row), ["the evidence has no packet"])
        self.assertEqual(verify_packet({"player_id": "p"}), ["the case has no evidence block"])

    def test_packet_digests_do_not_move_observation_ids(self):
        row = self.serialized("radar-friend")
        expected = [obs.observation_id for obs in self.by_id["radar-friend"].evidence]
        self.assertEqual([obs["observation_id"] for obs in row["evidence"]["observations"]], expected)


class HistoricalPacketTest(unittest.TestCase):
    """Cases written by an earlier fpsdet must keep verifying: a recipe never changes its meaning."""

    def test_p23_packets_still_verify(self):
        from pathlib import Path

        fixture = json.loads((Path(__file__).resolve().parent / "fixtures" / "historical-packets-p23.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["cases"]), 2)
        for case in fixture["cases"]:
            self.assertEqual(case["evidence"]["provenance"]["inputs"]["recipe"], "fpsdet.player-events/1")
            self.assertEqual(verify_packet(case), [], case["player_id"])
            edited = copy.deepcopy(case)
            edited["decision"] = "clean" if case["decision"] != "clean" else "watch"
            self.assertTrue(verify_packet(edited))


if __name__ == "__main__":
    unittest.main()
