"""Active challenges, and the private replay they generalize.

LegacyPrivateReplaySemanticsTest was written before the challenge engine (P4, phase C0) to pin what the
private replay did, from the event field to the case: how ``private_track_ms`` is parsed, cut and agreed
on, the bar, the reason, the observation and the seal. The engine keeps all of it for events that name no
challenge. It no longer reads ``private_track_ms`` on an event that names one.

Every secret here is drawn fresh with os.urandom when the test runs, except TEST_VECTOR_KEY: the bytes
0 to 31, public, used only to pin the derivation recipe so it cannot drift without a failing test.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import hmac
import io
import json
import os
import pickle
import stat
import tempfile
import unittest
from pathlib import Path

from fpsdet.challenge import (
    MAX_PER_MATCH,
    OCCLUDED_MOTION_REPLAY,
    Budget,
    ChallengeError,
    ChallengePlan,
    ChallengeRegistry,
    challenge_knowledge,
    plan_file_from_dict,
)
from fpsdet.challenge_plan import (
    SECRET_ENV,
    SecretError,
    ServerSecret,
    derive,
    load_secret,
    new_secret_file,
    plan_match,
    realize,
    reproduce,
    secret_from_hex,
)
from fpsdet.models import Event, GameProfile
from fpsdet.parse import ParseError, parse_event
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.provenance import verify_packet
from fpsdet.signals import evidence_seal
from fpsdet.summarize import summarize

GAME = GameProfile(game_id="g")  # vision and audio declared; 8 samples and 1200 ms; 40 shots before aim is scored
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def shot(t: int, pid: str = "x", match: str = "m1", weapon: str = "ak", **values) -> Event:
    fields = dict(game_id="g", match_id=match, player_id=pid, t_ms=t, event_type="shot",
                  skill_band="average", weapon_class="rifle", weapon_id=weapon)
    fields.update(values)
    return Event(**fields)


def replay_samples(events: list[Event], profile=GAME) -> dict[str, list[float]]:
    """Each aim key's private replay samples, as the scorer reads them."""
    (record,) = summarize(events, profile)
    return {weapon.weapon_key: weapon.private_track_ms for weapon in record.weapons}


def case_of(events: list[Event], profile=GAME, pid: str = "x") -> dict:
    return next(case_to_dict(case) for case in run_score(events, profile) if case.player_id == pid)


class LegacyPrivateReplaySemanticsTest(unittest.TestCase):
    """The private replay before the challenge engine. Shots are two seconds apart unless a test says otherwise."""

    def test_the_field_is_a_number_and_null_is_absent(self):
        base = {"game_id": "g", "match_id": "m", "player_id": "p", "t_ms": 1, "skill_band": "average"}
        self.assertEqual(parse_event({**base, "private_track_ms": 80}).private_track_ms, 80.0)
        self.assertIsInstance(parse_event({**base, "private_track_ms": 80}).private_track_ms, float)
        self.assertIsNone(parse_event({**base, "private_track_ms": None}).private_track_ms)
        self.assertIsNone(parse_event(base).private_track_ms)
        with self.assertRaises(ParseError):
            parse_event({**base, "private_track_ms": "80"})
        # The schema says at least 0; the parser takes a negative, and it adds nothing.
        self.assertEqual(parse_event({**base, "private_track_ms": -5}).private_track_ms, -5.0)

    def test_zero_and_negative_add_nothing(self):
        shots = [shot(0, private_track_ms=0.0), shot(2000, private_track_ms=-5.0), shot(4000, private_track_ms=80.0)]
        self.assertEqual(replay_samples(shots), {"rifle": [80.0]})

    def test_each_sample_is_cut_to_the_time_since_the_previous_moment_in_the_match(self):
        shots = [
            shot(0, private_track_ms=300.0),  # the first moment of a match is not cut
            shot(50, private_track_ms=300.0),  # 50 ms since the previous shot
            shot(2000, private_track_ms=300.0),
            shot(2100),  # a shot without the field still moves the clock
            shot(2130, private_track_ms=300.0),
        ]
        self.assertEqual(replay_samples(shots), {"rifle": [300.0, 50.0, 300.0, 30.0]})

    def test_a_new_match_starts_uncut(self):
        shots = [shot(0, private_track_ms=300.0), shot(10, match="m2", private_track_ms=300.0)]
        self.assertEqual(replay_samples(shots), {"rifle": [300.0, 300.0]})

    def test_shots_at_one_moment_agree_once_or_disagree_to_nothing(self):
        shots = [
            shot(0, private_track_ms=80.0),
            shot(0, private_track_ms=80.0, weapon="m4"),
            shot(2000, private_track_ms=80.0),
            shot(2000, private_track_ms=90.0),
        ]
        (record,) = summarize(shots, GAME)
        samples = {weapon.weapon_key: weapon.private_track_ms for weapon in record.weapons}
        self.assertEqual(sum(len(values) for values in samples.values()), 1)
        skipped = [weapon.knowledge_skipped.get("private_replay") for weapon in record.weapons]
        self.assertIn({"disagreed": 1}, skipped)

    def test_the_bar_is_eight_samples_and_1200_ms_on_one_aim_key(self):
        self.assertEqual(case_of([shot(i * 2000, private_track_ms=150.0) for i in range(8)])["decision"], "review")
        self.assertNotEqual(case_of([shot(i * 2000, private_track_ms=149.0) for i in range(8)])["decision"], "review")
        self.assertNotEqual(case_of([shot(i * 2000, private_track_ms=400.0) for i in range(7)])["decision"], "review")
        self.assertNotEqual(case_of([shot(0, private_track_ms=5000.0)])["decision"], "review")

    def test_aim_keys_are_not_pooled(self):
        shots = [shot(i * 2000, private_track_ms=200.0, weapon_class="rifle") for i in range(6)]
        shots += [shot(20000 + i * 2000, private_track_ms=200.0, weapon_class="smg") for i in range(6)]
        self.assertEqual(sorted(replay_samples(shots)), ["rifle", "smg"])
        self.assertNotEqual(case_of(shots)["decision"], "review")

    def test_matches_are_pooled_on_one_aim_key(self):
        # Every match in the window adds to one total. Unrelated crossings in many matches can add up.
        shots = [shot(i * 2000, private_track_ms=200.0) for i in range(4)]
        shots += [shot(i * 2000, match="m2", private_track_ms=200.0) for i in range(4)]
        self.assertEqual(case_of(shots)["decision"], "review")

    def test_the_case(self):
        shots = [shot(i * 2000, private_track_ms=200.0) for i in range(10)] + [shot(0, match="m0")]
        case = case_of(shots)
        self.assertEqual(case["decision"], "review")
        self.assertEqual(case["automated_action"], "none")
        self.assertEqual(case["reasons"], ["rifle aim stayed on a private replay for 2000 ms across 10 shots"])
        self.assertEqual(case["checks"], ["private_replay"])
        self.assertEqual(case["seal"], "b9f2139acc3ba01f388dc0cd546d589e0bc0d2dca96b11c58afe6c6f1f07ee90")
        (obs,) = case["evidence"]["observations"]
        self.assertEqual((obs["family"], obs["kind"], obs["role"], obs["key"]), ("information", "private_replay", "review", "rifle"))
        # Every match the aim key was used in, not only the ones with replay time.
        self.assertEqual(obs["match_ids"], ["m0", "m1"])
        self.assertEqual(obs["evidence"], {"shots": 10, "total_ms": 2000.0, "thresholds": {"min_shots": 8, "min_total_ms": 1200}})
        self.assertEqual(obs["observation_id"], "obs-f527f69402a53ff6e8be4f41")
        self.assertEqual(obs["context"]["knowledge"]["status"], "unknowable")
        self.assertEqual(obs["context"]["knowledge"]["channels"], {"audio": "absent", "recent_perception": "not_applicable", "vision": "absent"})
        # Under 40 shots, aim is not scored, and the replay still is.
        self.assertIn("rifle: 11 shots, need 40 before aim is scored", case["observations"])

    def test_the_legacy_field_names_no_target(self):
        # Two replays in one match, or a replay and an ordinary hidden enemy, cannot be told apart in it.
        # Since the challenge engine a response names its challenge in challenge_id instead
        # (ChallengeLinkageTest); this field is still read as it was, on events that name no challenge.
        (record,) = summarize([shot(0, private_track_ms=80.0, enemy_id="e1", information_state="visible")], GAME)
        self.assertEqual(record.weapons[0].private_track_ms, [80.0])

    def test_the_planted_replay_lock(self):
        from fpsdet.synthetic import build_demo

        case = next(case_to_dict(case) for case in build_demo().cases if case.player_id == "replay-lock")
        self.assertEqual(case["reasons"], ["rifle aim stayed on a private replay for 1600 ms across 20 shots"])
        self.assertEqual(case["seal"], "e73f0a73ef538ed1667e4bb7938b487d3de12323b3369441585381cf35b9e3ae")
        self.assertEqual(case["seal"], evidence_seal_of(case))
        (obs,) = case["evidence"]["observations"]
        self.assertEqual((obs["family"], obs["kind"], obs["observation_id"]), ("information", "private_replay", "obs-952d9e164294a9c5fa31861a"))

    def test_a_p3_replay_packet_verifies(self):
        fixture = json.loads((FIXTURES / "historical-packets-p3.json").read_text(encoding="utf-8"))
        (case,) = fixture["cases"]
        self.assertEqual(case["evidence"]["observations"][0]["kind"], "private_replay")
        self.assertEqual(verify_packet(case), [])


TEST_VECTOR_KEY = bytes(range(32))  # public; pins the recipe; never a real secret
NONCE = "00112233445566778899aabbccddeeff"
BUDGET = Budget(to_ms=600_000)


def fresh() -> tuple[bytes, ServerSecret]:
    key = os.urandom(32)
    return key, ServerSecret(key)


def leaks(text: str, *secrets: bytes) -> list[str]:
    """Each way a secret could be spelled in text, that the text contains: hex either case, either half
    of the hex, standard and URL-safe base64."""
    found = []
    for key in secrets:
        spelled = key.hex()
        for form in (spelled, spelled.upper(), spelled[: len(spelled) // 2], spelled[len(spelled) // 2 :],
                     base64.b64encode(key).decode(), base64.urlsafe_b64encode(key).decode()):
            if form in text or form.lower() in text.lower():
                found.append(form[:6] + "...")
    return found


def run_cli(*argv: str) -> tuple[int, str]:
    from fpsdet.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as stop:
            code = stop.code if isinstance(stop.code, int) else 1
            err.write(str(stop.code))
    return code, out.getvalue() + err.getvalue()


def _framed(*parts) -> bytes:
    return b"".join(len(p := (part if isinstance(part, bytes) else part.encode())).to_bytes(4, "big") + p for part in parts)


class DerivationTest(unittest.TestCase):
    """HMAC-SHA256 keyed with the server secret, over a framed, versioned, domain-separated message."""

    def one(self, secret: ServerSecret, *, game="g", match="m1", subject="x", nonce=NONCE, spec=OCCLUDED_MOTION_REPLAY, budget=BUDGET):
        ((plan, realization),) = derive(secret, game, match, subject, nonce, spec, budget)
        return plan, realization

    def test_the_recipe_is_pinned(self):
        plan, realization = self.one(ServerSecret(TEST_VECTOR_KEY))
        self.assertEqual(plan.challenge_id, "ch-2167d11b4beef7b5f1d4a268")
        self.assertEqual((plan.start_ms, plan.end_ms), (397250, 409594))
        self.assertEqual(plan.commitment, "sha256:e53f8ea7b84ff9f233c97837ea2768c271da8ce4aca72dbefd01d6d1c58cedf5")
        self.assertEqual(plan.digest, "sha256:5ba47fb1b6be614f9d3dc8dbac5e6961a8e157bcd03669c4a03f769709535143")
        self.assertEqual(realization.parameter("heading_offset_deg"), 151)
        self.assertEqual(realization.parameter("replay_delay_ms"), 13036)

    def test_the_recipe_is_what_the_docs_say(self):
        # An independent build of the documented recipe, from hmac and hashlib alone.
        plan, realization = self.one(ServerSecret(TEST_VECTOR_KEY))
        head = ("fpsdet.challenge/1",)
        context = ("g", "m1", "x", NONCE, "occluded_motion_replay", "1")
        mac = lambda purpose, index: hmac.new(TEST_VECTOR_KEY, _framed(*head, purpose, *context, str(index)), hashlib.sha256).digest()
        self.assertEqual(plan.challenge_id, "ch-" + mac("id", 0).hex()[:24])
        material = mac("realization", 0)
        self.assertEqual(realization.material, material)
        draw = int.from_bytes(hmac.new(material, _framed("fpsdet.challenge/1", "parameter", "heading_offset_deg"), hashlib.sha256).digest(), "big")
        self.assertEqual(realization.parameter("heading_offset_deg"), 30 + draw % 301)
        length = 8_000 + int.from_bytes(mac("schedule", 0), "big") % 8_001
        self.assertEqual(plan.end_ms - plan.start_ms, length)
        public = json.dumps(plan.committed_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(plan.commitment, "sha256:" + hashlib.sha256(_framed("fpsdet.challenge-commitment/1", public, material)).hexdigest())

    def test_1_same_secret_and_context_same_realization(self):
        _key, secret = fresh()
        self.assertEqual(self.one(secret), self.one(secret))
        self.assertEqual(self.one(secret)[1].material, self.one(ServerSecret(_key))[1].material)

    def test_2_to_6_every_input_changes_the_realization(self):
        _key, secret = fresh()
        plan, realization = self.one(secret)
        other_spec = dataclasses.replace(OCCLUDED_MOTION_REPLAY, challenge_type="another_type")
        variants = {
            "player": self.one(secret, subject="y"),
            "match": self.one(secret, match="m2"),
            "challenge type": self.one(secret, spec=other_spec),
            "spec version": self.one(secret, spec=dataclasses.replace(OCCLUDED_MOTION_REPLAY, version=2)),
            "nonce": self.one(secret, nonce="ff" * 16),
            "game": self.one(secret, game="g2"),
            "secret": self.one(fresh()[1]),
        }
        for name, (other_plan, other) in variants.items():
            with self.subTest(name):
                self.assertNotEqual(other.material, realization.material)
                self.assertNotEqual(other_plan.challenge_id, plan.challenge_id)
                self.assertNotEqual(other_plan.commitment, plan.commitment)
        counters = derive(secret, "g", "m1", "x", NONCE, OCCLUDED_MOTION_REPLAY, Budget(to_ms=600_000, count=3))
        self.assertEqual(len({realization.material for _plan, realization in counters}), 3)
        self.assertEqual(len({plan.challenge_id for plan, _realization in counters}), 3)

    def test_7_the_commitment_moves_with_the_realization(self):
        from fpsdet.challenge_plan import commitment

        plan, realization = self.one(fresh()[1])
        self.assertEqual(commitment(plan.committed_fields(), realization.material), plan.commitment)
        flipped = bytes([realization.material[0] ^ 1]) + realization.material[1:]
        self.assertNotEqual(commitment(plan.committed_fields(), flipped), plan.commitment)
        moved = {**plan.committed_fields(), "start_ms": plan.start_ms + 1}
        self.assertNotEqual(commitment(moved, realization.material), plan.commitment)

    def test_8_the_id_depends_on_who_where_and_which_not_on_the_budget(self):
        _key, secret = fresh()
        plan, _ = self.one(secret)
        later, _ = self.one(secret, budget=Budget(to_ms=900_000, cooldown_ms=30_000, min_duration_ms=9_000))
        self.assertEqual(later.challenge_id, plan.challenge_id)
        self.assertNotEqual((later.start_ms, later.commitment), (plan.start_ms, plan.commitment))

    def test_16_public_data_alone_does_not_reproduce_a_realization(self):
        # Not a proof: HMAC-SHA256's security is. These are the shortcuts a reader of the source might try.
        from fpsdet.challenge_plan import commitment, message

        _key, secret = fresh()
        plan, realization = self.one(secret)
        canonical = json.dumps(plan.to_dict(), sort_keys=True).encode()
        framed = message("realization", "g", "m1", "x", NONCE, OCCLUDED_MOTION_REPLAY, 0)
        guesses = [
            hashlib.sha256(framed).digest(),
            hmac.new(b"", framed, hashlib.sha256).digest(),
            hmac.new(NONCE.encode(), framed, hashlib.sha256).digest(),
            hmac.new(bytes.fromhex(plan.commitment[7:]), framed, hashlib.sha256).digest(),
            hmac.new(bytes.fromhex(plan.digest[7:]), framed, hashlib.sha256).digest(),
            hashlib.sha256(canonical).digest(),
            hashlib.sha256(canonical + framed).digest(),
        ]
        for guess in guesses:
            self.assertNotEqual(guess, realization.material)
            self.assertNotEqual(commitment(plan.committed_fields(), guess), plan.commitment)
        id_guess = "ch-" + hashlib.sha256(message("id", "g", "m1", "x", NONCE, OCCLUDED_MOTION_REPLAY, 0)).hexdigest()[:24]
        self.assertNotEqual(id_guess, plan.challenge_id)

    def test_the_realization_is_never_serialized(self):
        _plan, realization = self.one(fresh()[1])
        self.assertFalse(hasattr(realization, "to_dict"))
        self.assertNotIn(realization.material.hex(), repr(realization))
        self.assertNotIn(str(realization.parameter("route_pick")), repr(realization))

    def test_the_game_server_gets_the_realization_back_from_the_public_record(self):
        _key, secret = fresh()
        plan, realization = self.one(secret)
        self.assertEqual(realize(secret, plan, BUDGET), realization)
        with self.assertRaises(ChallengeError):
            realize(fresh()[1], plan, BUDGET)
        with self.assertRaises(ChallengeError):
            realize(secret, dataclasses.replace(plan, end_ms=plan.end_ms + 1), BUDGET)


class SecretHandlingTest(unittest.TestCase):
    def test_the_secret_object_never_shows_its_key(self):
        key, secret = fresh()
        for text in (repr(secret), str(secret), f"{secret}", f"{secret!r}", "%s" % (secret,), repr([secret])):
            self.assertEqual(leaks(text, key), [])
            self.assertIn("redacted", text)
        with self.assertRaises(TypeError):
            pickle.dumps(secret)
        with self.assertRaises(SecretError):
            ServerSecret(b"short")

    def test_15_errors_never_contain_the_secret(self):
        key = os.urandom(32)
        bad = key.hex()[:-1] + "z"  # one non-hex digit in an otherwise real secret
        with self.assertRaises(SecretError) as caught:
            secret_from_hex(bad)
        self.assertEqual(leaks(str(caught.exception) + repr(caught.exception), key), [])
        # A traceback does not print the parser's own error, and that error names a position, not the text.
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(leaks(str(caught.exception.__context__), key), [])
        with self.assertRaises(SecretError) as caught:
            secret_from_hex(key.hex()[:40])  # too short
        self.assertEqual(leaks(str(caught.exception), key), [])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "key"
            path.write_text(bad)
            with self.assertRaises(SecretError) as caught:
                load_secret(path, environ={})
            self.assertEqual(leaks(str(caught.exception), key), [])
            with self.assertRaises(SecretError) as caught:
                load_secret(path, environ={SECRET_ENV: key.hex()})
            self.assertEqual(leaks(str(caught.exception), key), [])

    def test_the_secret_comes_from_a_file_or_the_environment(self):
        key = os.urandom(32)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "key"
            path.write_text(key.hex() + "\n")
            os.chmod(path, 0o600)
            secret, warnings = load_secret(path, environ={})
            self.assertEqual(warnings, [])
            self.assertEqual(secret.mac(b"m"), ServerSecret(key).mac(b"m"))
            os.chmod(path, 0o644)
            _secret, warnings = load_secret(path, environ={})
            self.assertTrue(warnings)
            self.assertEqual(leaks(" ".join(warnings), key), [])
        secret, _ = load_secret(None, environ={SECRET_ENV: key.hex()})
        self.assertEqual(secret.mac(b"m"), ServerSecret(key).mac(b"m"))
        with self.assertRaises(SecretError):
            load_secret(None, environ={})

    def test_keygen_writes_a_new_owner_only_file_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "key.hex"
            code, printed = run_cli("challenge", "keygen", "--out", str(path))
            self.assertEqual(code, 0)
            key = bytes.fromhex(path.read_text().strip())
            self.assertEqual(len(key), 32)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
            self.assertEqual(leaks(printed, key), [])
            code, printed = run_cli("challenge", "keygen", "--out", str(path))
            self.assertNotEqual(code, 0)
            self.assertEqual(bytes.fromhex(path.read_text().strip()), key)
            self.assertEqual(leaks(printed, key), [])

    def test_scoring_never_imports_the_secret_module(self):
        from fpsdet.provenance import DETECTOR_MODULES, NOT_DETECTOR

        self.assertNotIn("fpsdet.challenge_plan", DETECTOR_MODULES)
        self.assertIn("fpsdet.challenge_plan", NOT_DETECTOR)


class ScheduleTest(unittest.TestCase):
    def plan(self, secret, profile=GAME, **budget):
        return plan_match(secret, profile, "m1", ["a", "b", "c"], Budget(**{"to_ms": 1_500_000, **budget}), nonce=NONCE)

    def test_windows_keep_the_budget(self):
        _key, secret = fresh()
        planned = self.plan(secret, count=4, cooldown_ms=90_000)
        self.assertEqual(len(planned.plans), 12)
        by_subject: dict[str, list[ChallengePlan]] = {}
        for plan in planned.plans:
            by_subject.setdefault(plan.subject_id, []).append(plan)
            self.assertGreaterEqual(plan.start_ms, 60_000)
            self.assertLessEqual(plan.end_ms, 1_500_000)
            self.assertTrue(8_000 <= plan.end_ms - plan.start_ms <= 16_000)
        for rows in by_subject.values():
            self.assertEqual([plan.counter for plan in rows], [0, 1, 2, 3])
            for before, after in zip(rows, rows[1:]):
                self.assertGreaterEqual(after.start_ms - before.end_ms, 90_000)
        self.assertEqual(len({plan.challenge_id for plan in planned.plans}), 12)

    def test_the_budget_is_bounded(self):
        _key, secret = fresh()
        with self.assertRaises(ChallengeError):
            self.plan(secret, count=MAX_PER_MATCH + 1)
        with self.assertRaises(ChallengeError) as caught:
            self.plan(secret, count=4, to_ms=200_000)
        self.assertIn("fits at most", str(caught.exception))
        with self.assertRaises(ChallengeError):
            self.plan(secret, min_duration_ms=1_000, max_duration_ms=4_000)  # a window under 1200 ms can never reach the bar
        with self.assertRaises(ChallengeError):
            plan_match(secret, GAME, "m1", ["a", "a"], Budget(to_ms=1_500_000), nonce=NONCE)

    def test_a_channel_the_challenge_does_not_defeat_is_refused_at_planning(self):
        _key, secret = fresh()
        radar = dataclasses.replace(GAME, knowledge_channels=("vision", "audio", "radar"))
        with self.assertRaises(ChallengeError) as caught:
            self.plan(secret, profile=radar)
        self.assertIn("radar", str(caught.exception))
        self.assertEqual(challenge_knowledge(OCCLUDED_MOTION_REPLAY, radar).status, "unknown")
        self.assertEqual(challenge_knowledge(OCCLUDED_MOTION_REPLAY, GAME).status, "unknowable")

    def test_reproducible_with_a_nonce_and_fresh_without(self):
        _key, secret = fresh()
        self.assertEqual(self.plan(secret), self.plan(secret))
        first = plan_match(secret, GAME, "m1", ["a"], Budget(to_ms=1_500_000))
        second = plan_match(secret, GAME, "m1", ["a"], Budget(to_ms=1_500_000))
        self.assertNotEqual(first.nonce, second.nonce)
        self.assertNotEqual(first.plans[0].challenge_id, second.plans[0].challenge_id)

    def test_no_realization_repeats_across_ten_thousand_challenges(self):
        _key, secret = fresh()
        materials, ids, routes = set(), set(), set()
        budget = Budget(to_ms=1_500_000, count=4, cooldown_ms=30_000)
        for match in range(50):
            for subject in range(50):
                for plan, realization in derive(secret, "g", f"m{match}", f"p{subject}", NONCE, OCCLUDED_MOTION_REPLAY, budget):
                    materials.add(realization.material)
                    ids.add(plan.challenge_id)
                    routes.add(realization.parameters)
        self.assertEqual((len(materials), len(ids), len(routes)), (10_000, 10_000, 10_000))


class PlanFileTest(unittest.TestCase):
    def planned(self, secret):
        return plan_match(secret, GAME, "m1", ["a", "b"], Budget(to_ms=1_500_000, count=2), nonce=NONCE)

    def test_a_plan_file_round_trips_and_checks_without_the_secret(self):
        _key, secret = fresh()
        planned = self.planned(secret)
        again = plan_file_from_dict(json.loads(json.dumps(planned.to_dict())))
        self.assertEqual(again, planned)
        self.assertEqual(len(ChallengeRegistry.from_files([again])), 4)

    def test_an_edited_record_fails_its_digest(self):
        _key, secret = fresh()
        raw = self.planned(secret).to_dict()
        raw["challenges"][0]["end_ms"] += 1000
        with self.assertRaises(ChallengeError):
            plan_file_from_dict(raw)

    def test_an_edit_with_a_fresh_digest_is_caught_only_with_the_secret(self):
        _key, secret = fresh()
        planned = self.planned(secret)
        forged = dataclasses.replace(planned.plans[0], end_ms=planned.plans[0].end_ms - 500)
        raw = planned.to_dict()
        raw["challenges"][0] = forged.to_dict()
        public = plan_file_from_dict(raw)  # consistent: the digest is not a signature
        problems = reproduce(secret, public)
        self.assertEqual([p.split(": ", 1)[1] for p in problems], ["end_ms is not what this secret plans"])
        self.assertEqual(reproduce(secret, planned), [])
        self.assertTrue(reproduce(fresh()[1], planned))

    def test_overlaps_duplicates_and_unknown_types_are_refused(self):
        _key, secret = fresh()
        planned = self.planned(secret)
        raw = planned.to_dict()
        raw["challenges"].append(raw["challenges"][0])
        with self.assertRaises(ChallengeError):
            plan_file_from_dict(raw)
        close = dataclasses.replace(planned.plans[1], start_ms=planned.plans[0].end_ms + 10, end_ms=planned.plans[0].end_ms + 9_000)
        raw = planned.to_dict()
        raw["challenges"][1] = close.to_dict()
        with self.assertRaises(ChallengeError):
            plan_file_from_dict(raw)
        raw = planned.to_dict()
        raw["version"] = 9
        with self.assertRaises(ChallengeError):
            plan_file_from_dict(raw)
        with self.assertRaises(ChallengeError):
            ChallengeRegistry([planned.plans[0], planned.plans[0]])


class PlanOutputLeakTest(unittest.TestCase):
    def test_9_the_plan_and_the_commands_never_print_the_secret_or_a_realization(self):
        key = os.urandom(32)
        with tempfile.TemporaryDirectory() as folder:
            secret_path = Path(folder) / "key.hex"
            secret_path.write_text(key.hex())
            os.chmod(secret_path, 0o600)
            plan_path = Path(folder) / "plan.json"
            profile = str(Path(__file__).resolve().parents[1] / "profiles" / "example-loadout.json")
            code, printed = run_cli("challenge", "plan", "--profile", profile, "--match", "m-1", "--player", "p-7",
                                    "--player", "p-9", "--count", "3", "--to-ms", "1500000", "--secret-file", str(secret_path), "--out", str(plan_path))
            self.assertEqual(code, 0, printed)
            written = plan_path.read_text()
            planned = plan_file_from_dict(json.loads(written))
            materials = [realize(ServerSecret(key), plan, planned.budget).material for plan in planned.plans]
            self.assertEqual(len(materials), 6)
            for name, text in (("plan file", written), ("plan output", printed)):
                self.assertEqual(leaks(text, key, *materials), [], name)
            for argv in (("challenge", "verify", str(plan_path)), ("challenge", "verify", str(plan_path), "--secret-file", str(secret_path))):
                code, printed = run_cli(*argv)
                self.assertEqual(code, 0, printed)
                self.assertEqual(leaks(printed, key, *materials), [])
            other = Path(folder) / "other.hex"
            other.write_text(os.urandom(32).hex())
            code, printed = run_cli("challenge", "verify", str(plan_path), "--secret-file", str(other))
            self.assertEqual(code, 1)
            self.assertEqual(leaks(printed, key, *materials, bytes.fromhex(other.read_text())), [])
            code, printed = run_cli("challenge", "plan", "--profile", profile, "--match", "m-1", "--player", "p-7", "--count", "9",
                                    "--to-ms", "1500000", "--secret-file", str(secret_path), "--out", str(plan_path))
            self.assertNotEqual(code, 0)
            self.assertEqual(leaks(printed, key), [])
            secret_path.write_text(key.hex()[:-2] + "zz")
            code, printed = run_cli("challenge", "plan", "--profile", profile, "--match", "m-1", "--player", "p-7",
                                    "--to-ms", "1500000", "--secret-file", str(secret_path), "--out", str(plan_path))
            self.assertNotEqual(code, 0)
            self.assertEqual(leaks(printed, key), [])

    def test_the_types_command_prints_the_spec_and_no_secret(self):
        code, printed = run_cli("challenge", "types")
        self.assertEqual(code, 0)
        (spec,) = json.loads(printed)
        self.assertEqual((spec["challenge_type"], spec["defeats"]), ("occluded_motion_replay", ["vision", "audio"]))
        self.assertTrue(spec["capability"]["limits"])


def plan_for(subject: str = "x", match: str = "m1", count: int = 1, profile=GAME, secret: ServerSecret | None = None):
    """A real plan from a fresh secret, for one player in one match, with its windows wherever they fell."""
    secret = secret or fresh()[1]
    return plan_match(secret, profile, match, [subject], Budget(to_ms=1_500_000, count=count), nonce=os.urandom(16).hex())


def follow(plan: ChallengePlan, n: int = 20, every: int = 100, track: float | None = None, pid: str | None = None,
           offset: int = 0, kind: str = "shot", **fields) -> list[Event]:
    """n events every `every` ms from the window's start (plus offset), each naming the challenge."""
    rows = []
    for index in range(1, n + 1):
        values = {"challenge_id": plan.challenge_id, "challenge_track_ms": float(every if track is None else track), **fields}
        rows.append(shot(plan.start_ms + offset + index * every, pid=pid or plan.subject_id, match=plan.match_id, event_type=kind, **values))
    return rows


def scored_with(events: list[Event], files=(), profile=GAME, pid: str = "x") -> dict:
    registry = ChallengeRegistry.from_files(files) if files is not None else None
    return next(case_to_dict(case) for case in run_score(events, profile, challenges=registry) if case.player_id == pid)


def results_of(case: dict) -> dict[str, dict]:
    return {row["challenge_id"]: row for row in case["evidence"].get("challenges", [])}


class ChallengeLinkageTest(unittest.TestCase):
    """A response counts only for the exact challenge it names, planned for this player, in its match and window."""

    def test_a_followed_challenge_is_one_result(self):
        planned = plan_for()
        (plan,) = planned.plans
        result = results_of(scored_with(follow(plan), [planned]))[plan.challenge_id]
        self.assertEqual(result["status"], "followed")
        self.assertEqual((result["eligible_samples"], result["tracked_samples"], result["total_ms"]), (20, 20, 2000.0))
        self.assertEqual((result["plan"], result["match_id"], result["not_counted"]), (plan.digest, "m1", {}))

    def test_time_is_cut_to_the_previous_naming_event_and_to_the_window(self):
        planned = plan_for()
        (plan,) = planned.plans
        events = follow(plan, n=10, every=30, track=300.0)
        (record,) = summarize(events, GAME)
        from fpsdet.challenge import evaluate_challenges

        (result,), _ = evaluate_challenges("x", {"m1"}, record.challenge_samples, ChallengeRegistry(planned.plans), GAME)
        self.assertEqual(result.tracked, [30.0] * 10)  # the first one too: 30 ms since the window opened

    def test_outside_the_window_does_not_count(self):
        planned = plan_for()
        (plan,) = planned.plans
        early = follow(plan, n=5, offset=-1000)  # the window opens after these
        late = [dataclasses.replace(event, t_ms=plan.end_ms + 1 + i) for i, event in enumerate(follow(plan, n=5))]
        result = results_of(scored_with(early + late, [planned]))[plan.challenge_id]
        self.assertEqual(result["not_counted"], {"outside_window": 10})
        self.assertEqual((result["status"], result["tracked_samples"]), ("no_samples", 0))

    def test_an_unknown_challenge_is_unplanned_and_noted(self):
        planned = plan_for()
        (plan,) = planned.plans
        for files in (None, [], [plan_for(match="m9")]):
            with self.subTest(files=files):
                case = scored_with(follow(plan), files)
                result = results_of(case)[plan.challenge_id]
                self.assertEqual((result["status"], result["plan"], result["tracked_samples"]), ("unplanned", None, 0))
                self.assertEqual(result["not_counted"], {"unplanned": 20})
                self.assertTrue(any("no plan for" in line for line in case["observations"]))

    def test_a_challenge_planned_for_another_player_is_not_read(self):
        planned = plan_for(subject="someone-else")
        (plan,) = planned.plans
        case = scored_with(follow(plan, pid="x"), [planned])
        result = results_of(case)[plan.challenge_id]
        self.assertEqual((result["status"], result["cause"], result["tracked_samples"]), ("abstained", "other_subject", 0))
        self.assertTrue(any("planned for another player" in line for line in case["observations"]))

    def test_a_stale_id_from_another_match_is_not_read(self):
        planned = plan_for(match="m1")
        (plan,) = planned.plans
        stale = [dataclasses.replace(event, match_id="m2") for event in follow(plan)]
        result = results_of(scored_with(stale, [planned]))[plan.challenge_id]
        self.assertEqual(result["not_counted"], {"other_match": 20})
        self.assertEqual(result["tracked_samples"], 0)

    def test_duplicates_count_once_and_disagreement_counts_nothing(self):
        planned = plan_for()
        (plan,) = planned.plans
        events = follow(plan, n=10)
        doubled = events + [dataclasses.replace(event) for event in events]
        result = results_of(scored_with(doubled, [planned]))[plan.challenge_id]
        self.assertEqual((result["eligible_samples"], result["tracked_samples"]), (10, 10))
        torn = events + [dataclasses.replace(event, challenge_track_ms=50.0) for event in events[:4]]
        result = results_of(scored_with(torn, [planned]))[plan.challenge_id]
        self.assertEqual((result["tracked_samples"], result["not_counted"]), (6, {"disagreed": 4}))

    def test_challenge_time_with_no_id_is_not_read(self):
        planned = plan_for()
        (plan,) = planned.plans
        events = [dataclasses.replace(event, challenge_id=None) for event in follow(plan)]
        case = scored_with(events, [planned])
        self.assertEqual(results_of(case)[plan.challenge_id]["status"], "no_samples")
        self.assertTrue(any("with no challenge_id" in line for line in case["observations"]))

    def test_the_legacy_field_is_not_read_on_an_event_that_names_a_challenge(self):
        planned = plan_for()
        (plan,) = planned.plans
        events = [dataclasses.replace(event, challenge_track_ms=None, private_track_ms=200.0) for event in follow(plan)]
        (record,) = summarize(events, GAME)
        self.assertEqual(record.weapons[0].private_track_ms, [])
        result = results_of(scored_with(events, [planned]))[plan.challenge_id]
        self.assertEqual((result["tracked_samples"], result["not_counted"]), (0, {"no_measurement": 20}))

    def test_a_real_enemy_the_client_could_know_explains_the_sample(self):
        planned = plan_for()
        (plan,) = planned.plans
        cases = {
            "seen": {"enemy_id": "e1", "information_state": "visible"},
            "heard": {"enemy_id": "e1", "information_state": "audio"},
            "recent": {"enemy_id": "e1", "information_state": "unknowable", "since_perceived_ms": 200.0},
            "unchecked": {"enemy_id": "e1"},
            "conflict": {"enemy_id": "e1", "information_state": "visible", "vision_state": "absent"},
        }
        for cause, fields in cases.items():
            with self.subTest(cause):
                result = results_of(scored_with(follow(plan, **fields), [planned]))[plan.challenge_id]
                self.assertEqual((result["tracked_samples"], result["not_counted"]), (0, {cause: 20}))
                self.assertEqual(result["status"], "not_followed")
        hidden = follow(plan, enemy_id="e1", information_state="unknowable", since_perceived_ms=5000.0)
        self.assertEqual(results_of(scored_with(hidden, [planned]))[plan.challenge_id]["status"], "followed")

    def test_a_declared_channel_the_challenge_does_not_defeat_abstains(self):
        planned = plan_for()
        (plan,) = planned.plans
        for channel in ("radar", "team_share", "ability", "recent_perception"):
            with self.subTest(channel):
                profile = dataclasses.replace(GAME, knowledge_channels=("vision", "audio", channel))
                result = results_of(scored_with(follow(plan), [planned], profile))[plan.challenge_id]
                if channel == "recent_perception":  # never perceivable: not applicable, so still unknowable
                    self.assertEqual(result["status"], "followed")
                    continue
                self.assertEqual((result["status"], result["cause"], result["tracked_samples"]), ("abstained", "unchecked", 0))
                self.assertEqual(result["not_counted"], {"unchecked": 20})

    def test_a_planned_challenge_with_no_response_is_listed(self):
        planned = plan_for(count=2)
        first, second = planned.plans
        elsewhere = plan_for(match="m7")
        case = scored_with(follow(first), [planned, elsewhere])
        results = results_of(case)
        self.assertEqual(sorted(results), sorted([first.challenge_id, second.challenge_id]))
        self.assertEqual(results[second.challenge_id]["status"], "no_samples")

    def test_movement_samples_count_like_shots(self):
        planned = plan_for()
        (plan,) = planned.plans
        events = follow(plan, kind="movement")
        self.assertEqual(results_of(scored_with(events, [planned]))[plan.challenge_id]["status"], "followed")

    def test_no_challenge_telemetry_and_no_plan_here_no_block(self):
        for files in (None, [plan_for(match="m9")], [plan_for(subject="y")]):
            with self.subTest(files=files):
                case = scored_with([shot(i * 2000) for i in range(5)], files)
                self.assertNotIn("challenges", case["evidence"])

    def test_parsing(self):
        base = {"game_id": "g", "match_id": "m", "player_id": "p", "t_ms": 1}
        event = parse_event({**base, "challenge_id": "ch-0123456789abcdef01234567", "challenge_track_ms": 80})
        self.assertEqual((event.challenge_id, event.challenge_track_ms), ("ch-0123456789abcdef01234567", 80.0))
        for bad in ("<script>", "a b", "", "-x", "x" * 129, 7):
            with self.subTest(bad=bad), self.assertRaises(ParseError):
                parse_event({**base, "challenge_id": bad})
        with self.assertRaises(ParseError):
            parse_event({**base, "challenge_id": "c1", "challenge_track_ms": "80"})


def evidence_seal_of(row: dict) -> str:
    from fpsdet.models import Case

    return evidence_seal(Case(player_id=row["player_id"], game_id=row["game_id"], decision=row["decision"],
                              recommended_action="", automated_action="none", skill_band="", reports=0, reasons=row["reasons"]))


if __name__ == "__main__":
    unittest.main()
