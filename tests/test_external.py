"""External evidence: records from other integrity systems, kept apart from fpsdet's own evidence.

NativeFusionSurfacesTest was written before external evidence (P5, phase E0) to pin the places it could
reach: what the AI brief is sent, how a batch watch moves a decision, that reports move nothing, what
packet/1 binds, and that a packet written before P5 verifies.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fpsdet.ai_triage import triage_case
from fpsdet.models import Case, Event, GameProfile
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.priority import review_order
from fpsdet.provenance import PACKET_PROVENANCE, packet_material, verify_packet
from fpsdet.synthetic import build_demo

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = build_demo()
GAME = GameProfile(game_id="g")


def shot(t: int, pid: str = "x", match: str = "m1", **values) -> Event:
    fields = dict(game_id="g", match_id=match, player_id=pid, t_ms=t, event_type="shot",
                  skill_band="average", weapon_class="rifle", weapon_id="ak")
    fields.update(values)
    return Event(**fields)


class NativeFusionSurfacesTest(unittest.TestCase):
    """The surfaces external evidence will meet, as they were before it."""

    def test_the_ai_brief_is_sent_these_case_fields_and_no_evidence(self):
        case = case_to_dict(next(c for c in DEMO.cases if c.player_id == "clone-buyer"))
        sent: list[dict] = []
        triage_case(case, lambda body: sent.append(body) or "brief", known_ids=[c.player_id for c in DEMO.cases])
        payload = json.loads(sent[0]["messages"][1]["content"])
        self.assertEqual(sorted(payload), [
            "ai_brief", "automated_action", "checks", "decision", "game_id", "inherit_lags_ms", "limits", "match_ids",
            "metrics", "observations", "party_ids", "party_note", "player_id", "queue_rank", "reasons",
            "recommended_action", "reports", "seal", "skill_band", "speed", "untrained", "vendor_r", "vendor_twin", "version",
        ])

    def test_a_batch_watch_moves_clean_to_watch_and_never_makes_a_review(self):
        from fpsdet.score import _watch_for_batch

        def case(decision: str) -> Case:
            return Case(player_id="x", game_id="g", decision=decision, recommended_action="", automated_action="none",
                        skill_band="average", reports=0, reasons=["native line"] if decision == "review" else [])

        clean, review = case("clean"), case("review")
        for target in (clean, review):
            _watch_for_batch(target, "batch line", 1, "leftover", {"partner": "y"})
        self.assertEqual((clean.decision, clean.reasons, clean.observations), ("watch", ["batch line"], []))
        self.assertEqual((review.decision, review.reasons, review.observations), ("review", ["native line"], ["batch line"]))
        self.assertEqual(review.automated_action, "none")

    def test_reports_across_a_run_move_no_decision_evidence_or_packet(self):
        events = [shot(i * 2000, pid=pid, private_track_ms=200.0 if pid == "x" else 0.0) for pid in ("x", "y") for i in range(10)]
        quiet = {c.player_id: case_to_dict(c) for c in run_score(events, GAME)}
        loud = {c.player_id: case_to_dict(c) for c in run_score(events, GAME, reports={"x": 50, "y": 50})}
        for pid in quiet:
            self.assertEqual(quiet[pid]["decision"], loud[pid]["decision"])
            self.assertEqual(quiet[pid]["evidence"], loud[pid]["evidence"])
            self.assertEqual(quiet[pid]["seal"], loud[pid]["seal"])

    def test_what_packet_1_binds(self):
        case = case_to_dict(next(c for c in DEMO.cases if c.player_id == "elite-human"))
        self.assertEqual(case["evidence"]["packet"]["recipe"], "fpsdet.packet/1")
        self.assertEqual(sorted(packet_material(case)), [
            "decision", "eligibility", "evidence_version", "game", "observations", "provenance", "provenance_version", "recipe", "subject",
        ])
        self.assertEqual(PACKET_PROVENANCE, {
            "detector": ("recipe", "digest", "modules"),
            "profile": ("recipe", "digest"),
            "cohort": ("mode", "recipe", "digest", "stored_digest", "integrity"),
            "inputs": ("recipe", "digest", "events", "matches"),
            "history": ("mode", "recipe", "digest", "windows"),
        })

    def test_review_order_is_decision_then_reports_then_player(self):
        def case(pid: str, decision: str, reports: int) -> Case:
            return Case(player_id=pid, game_id="g", decision=decision, recommended_action="", automated_action="none",
                        skill_band="average", reports=reports)

        cases = [case("a", "clean", 9), case("b", "watch", 0), case("c", "review", 0), case("d", "watch", 3)]
        self.assertEqual([c.player_id for c in review_order(cases)], ["c", "d", "b", "a"])

    def test_p4_packets_verify(self):
        fixture = json.loads((FIXTURES / "historical-packets-p4.json").read_text(encoding="utf-8"))
        self.assertEqual([case["player_id"] for case in fixture["cases"]], ["replay-lock", "elite-human", "clone-buyer"])
        for case in fixture["cases"]:
            self.assertEqual(case["evidence"]["packet"]["recipe"], "fpsdet.packet/1")
            self.assertEqual(verify_packet(case), [], case["player_id"])


if __name__ == "__main__":
    unittest.main()
