"""The fpsdet Arena (examples/fps-arena): a tiny reference FPS built to show what fpsdet sees.

The arena is a consumer of fpsdet, never a second implementation of it. These tests check that its scenarios
declare what they expect, that the committed qualification result and captures reproduce offline with the
real scorer, that every scenario came out as its metadata declared, that the server keeps the pilot's
knowledge queries and loopback rules, that the stock client knows nothing of challenges or the operator
feed, that AI and external records change nothing they must not, and that no capture carries a secret or
a person's or machine's identity. No engine is needed; the live qualification is run by hand.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARENA = ROOT / "examples" / "fps-arena"
GODOT = ARENA / "godot"
PILOT = ROOT / "examples" / "pilot" / "godot" / "scripts"

REQUIRED = ("normal_play", "impossible_speed", "fire_rate", "recoil_floor", "recoil_mirror", "audible_hidden_enemy", "unknowable_hidden_enemy",
            "unknowable_hidden_tracked", "unchecked_audio_channel", "wire_vs_picture", "active_challenge", "angle_hold_false_positive", "external_record")
CARD = ("what_this_tests", "player_can_know", "server_knows", "expected_fpsdet_behavior", "invalid_if")


def harness():
    if "arena_harness" not in sys.modules:
        spec = importlib.util.spec_from_file_location("arena_harness", ARENA / "harness" / "arena.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["arena_harness"] = module
        spec.loader.exec_module(module)
    return sys.modules["arena_harness"]


def result() -> dict:
    return json.loads((ARENA / "result.json").read_text(encoding="utf-8"))


def scenario_result(name: str) -> dict:
    return next(row for row in result()["scenarios"] if row["id"] == name)


def events(name: str) -> list[dict]:
    return [json.loads(line) for line in (ARENA / "captures" / name / "events.ndjson").read_text(encoding="utf-8").splitlines() if line.strip()]


class ScenarioMetadataTest(unittest.TestCase):
    """Expected behaviour lives in each scenario file as metadata, with a complete card; actual behaviour never does."""

    def test_every_required_scenario_exists_with_a_complete_card(self):
        found = harness().scenarios()
        for name in REQUIRED:
            self.assertIn(name, found)
            card = found[name]["card"]
            for field in CARD:
                self.assertTrue(card.get(field), f"{name}: {field}")
            self.assertTrue(found[name]["expected"], name)
            self.assertTrue(found[name]["instructions"], name)
            self.assertGreater(found[name]["duration_ms"], 0)
        keys = [spec["key"] for spec in found.values()]
        self.assertEqual(len(keys), len(set(keys)), "two scenarios share a key")

    def test_the_knowledge_scenarios_declare_the_three_answers(self):
        found = harness().scenarios()
        self.assertEqual(found["audible_hidden_enemy"]["expected"]["knowledge"], {"arena-bot-b": "known"})
        self.assertEqual(found["unknowable_hidden_tracked"]["expected"]["knowledge"], {"arena-bot-b": "unknowable"})
        self.assertEqual(found["unchecked_audio_channel"]["expected"]["knowledge"], {"arena-bot-b": "unknown"})
        self.assertFalse(found["unchecked_audio_channel"]["audio_query"])
        self.assertEqual(found["unchecked_audio_channel"]["expected"]["eligibility"]["hidden"], "telemetry_unavailable")
        for name in ("normal_play", "audible_hidden_enemy", "unknowable_hidden_enemy", "unchecked_audio_channel", "external_record"):
            self.assertTrue(found[name]["expected"].get("no_review"), name)

    def test_the_challenge_scenarios_are_marked_experimental_and_plan_version_2(self):
        found = harness().scenarios()
        for name in ("active_challenge", "angle_hold_false_positive"):
            self.assertTrue(found[name]["experimental"], name)
            self.assertEqual(found[name]["challenge"]["version"], 2)
            self.assertEqual(found[name]["expected"]["challenge"], {"status": "followed", "verification": "per_sample"})
        self.assertEqual(found["angle_hold_false_positive"]["standin"]["kind"], "holder")
        self.assertIn("not the same thing as responding to hidden information", found["angle_hold_false_positive"]["card"]["expected_fpsdet_behavior"])

    def test_the_profile_declares_vision_and_audio_and_counts_the_speed_run_in_samples(self):
        from fpsdet.parse import load_profile

        profile = load_profile(ARENA / "arena.json")
        self.assertEqual(profile.knowledge_channels, ("vision", "audio"))
        self.assertIsNone(profile.speed_min_run_ms)  # the known sample-count semantics, shown as they are, not changed here
        self.assertEqual((profile.hidden_track_min_ms, profile.hidden_track_min_samples), (1200, 8))
        self.assertEqual((profile.shot_clock, profile.tick_ms), ("server_tick", 17))


class ServerCodeTest(unittest.TestCase):
    """The Godot server derives realizations with fpsdet's recipe, keeps the pilot's knowledge queries byte for byte,
    listens on this machine only, and keeps the secret to itself. The stock client knows nothing of the operator feed."""

    def test_the_gdscript_recipe_is_fpsdets(self):
        from fpsdet.challenge import COMMITMENT_RECIPE, DERIVATION_RECIPE, OCCLUDED_MOTION_REPLAY_V2, PLAN_FILE_FORMAT, ChallengePlan

        source = (GODOT / "scripts" / "recipe.gd").read_text(encoding="utf-8")
        self.assertEqual(source, (PILOT / "recipe.gd").read_text(encoding="utf-8"))
        self.assertIn(f'DERIVATION_RECIPE := "{DERIVATION_RECIPE}"', source)
        self.assertIn(f'COMMITMENT_RECIPE := "{COMMITMENT_RECIPE}"', source)
        self.assertIn(f'PLAN_FORMAT := "{PLAN_FILE_FORMAT}"', source)
        ranges = re.findall(r'\["(\w+)", (\d+), (\d+)\]', source)
        self.assertEqual([(name, int(low), int(high)) for name, low, high in ranges], list(OCCLUDED_MOTION_REPLAY_V2.parameters))
        committed = re.search(r"const COMMITTED := \[(.*?)\]", source).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', committed), list(ChallengePlan("i", "t", 1, "g", "m", "s", 0, "n", 0, 1, "c").committed_fields()))

    def test_the_server_keeps_the_pilots_knowledge_queries(self):
        pilot = (PILOT / "server.gd").read_text(encoding="utf-8")
        arena = (GODOT / "scripts" / "arena_server.gd").read_text(encoding="utf-8")
        for name in ("CONE_DEG", "HEARING_RADIUS", "SOUND_MEMORY_TICKS", "STEP_EVERY_TICKS", "RESPONDER_LAG_TICKS", "VISION_MARGIN"):
            pattern = re.compile(rf"^const {name} := ([^#\n]+)", re.M)
            self.assertEqual(pattern.search(arena).group(1).strip(), pattern.search(pilot).group(1).strip(), name)

        def body(source: str, name: str) -> str:
            start = source.index(f"func {name}(")
            end = source.index("\n\n\n", start)
            return source[start:end]

        for name in ("_vision", "_body_points", "_audio", "_in_cone", "_worst", "_challenge_fields"):
            self.assertEqual(body(arena, name), body(pilot, name), name)
        self.assertIn('_end("perceivable")', arena)
        self.assertIn('accumulator["vision"] = _worst(', arena)

    def test_the_server_listens_on_loopback_and_never_writes_the_secret(self):
        server = (GODOT / "scripts" / "arena_server.gd").read_text(encoding="utf-8")
        self.assertIn('options.get("bind", "127.0.0.1")', server)
        self.assertIn("func _private(address: String)", server)
        self.assertIn("secret = PackedByteArray()", server)  # the key is dropped once the realizations are derived
        self.assertNotIn("get_peer_address", server)
        self.assertNotIn("get_remote_address", server)
        for public in ("_log(", "_write(", "store_string", "_write_json("):
            for line in server.splitlines():
                if public in line:
                    self.assertNotIn("secret", line.replace("secret-file", "").replace("no challenge secret", "").replace("loaded[1]", ""), line)
        # The server never reads what a client claims to have seen: commands are move, look and fire only.
        self.assertIn('players[name].cmd = {"move": move.limit_length(1.0), "yaw": wrapf(yaw, -180.0, 180.0), "pitch": clampf(pitch, -89.0, 89.0), "fire": fire}', server)
        self.assertNotIn('"hidden_track_ms": hidden', server)
        # Hidden time is measured only when the audio query ran: unchecked is never reported as absent.
        self.assertIn('if name == SUBJECT and bool(scenario.get("audio_query", true)):', server)

    def test_the_stock_client_knows_nothing_of_challenges_or_the_operator_feed(self):
        client = (GODOT / "scripts" / "arena_client.gd").read_text(encoding="utf-8")
        code = "\n".join(line for line in client.splitlines() if not line.strip().startswith("#"))
        self.assertNotIn("challenge", code.lower())
        self.assertNotIn("probe", code.lower())
        self.assertNotIn("operator", code.lower())
        for word in ("get_image", "save_png", "OS.execute", "DirAccess", "OS.get_environment", "get_unique_id"):
            self.assertNotIn(word, client)
        # Every remote body is drawn the same way, interpolated one delay behind the newest snapshot.
        self.assertIn("latest_ms - Arena.INTERP_DELAY_MS", client)
        main = (GODOT / "main.gd").read_text(encoding="utf-8")
        self.assertIn('"client":', main)
        self.assertNotIn("ServerOperator", main.split('"client":')[1].split('"operator":')[0])

    def test_the_operator_view_computes_no_knowledge_state_itself(self):
        view = (GODOT / "scripts" / "operator_view.gd").read_text(encoding="utf-8")
        # The three answers come from fpsdet's table, looked up by the server's channel words, never derived here.
        self.assertIn('table.get("enemy", {}).get(key, {})', view)
        self.assertIn('table.get("challenge_body", {})', view)
        code = "\n".join(line for line in view.splitlines() if not line.strip().startswith("#"))
        self.assertNotIn('"unknowable"', code.replace('"unknowable": "c084fc"', ""))  # never produced here, only coloured
        self.assertNotIn('"unknown"', code.replace('"unknown": "9aa4b2"', ""))
        # The one comparison the view makes is recent perception against the grace, and the grace is fpsdet's, from the table.
        self.assertIn('float(since_ms) < float(table.get("hidden_grace_ms", 1000))', code)
        self.assertEqual(code.count('"known" if'), 1)


class KnowledgeTableTest(unittest.TestCase):
    """The table the operator view looks up is fpsdet's own answer for every channel combination."""

    def test_the_table_matches_fpsdet(self):
        from fpsdet.knowledge import body_knowledge, resolve
        from fpsdet.challenge import OCCLUDED_MOTION_REPLAY_V2

        table = harness().knowledge_table()
        profile = harness().profile()
        self.assertEqual(table["required"], ["vision", "audio"])
        for key, found in table["enemy"].items():
            vision, audio, recent = key.split("|")
            state = resolve({"vision": vision, "audio": audio, "recent_perception": recent}, profile.knowledge_channels)
            self.assertEqual(found, {"status": state.status, "cause": state.cause}, key)
        for key, found in table["challenge_body"].items():
            vision, audio = key.split("|")
            state = body_knowledge((("audio", audio), ("vision", vision)), OCCLUDED_MOTION_REPLAY_V2.not_applicable, profile.knowledge_channels)
            self.assertEqual(found, {"status": state.status, "cause": state.cause}, key)
        self.assertEqual(table["enemy"]["absent|absent|unchecked"]["status"], "unknowable")
        self.assertEqual(table["enemy"]["absent|unchecked|unchecked"]["status"], "unknown")
        self.assertEqual(table["enemy"]["absent|known|unchecked"]["status"], "known")
        self.assertEqual(table["enemy"]["absent|absent|known"]["status"], "known")


class QualificationResultTest(unittest.TestCase):
    """examples/fps-arena/result.json (fpsdet.arena/1): what the live qualification found, scenario by scenario."""

    def test_every_scenario_came_out_as_declared_and_replayed_identically(self):
        found = result()
        self.assertEqual((found["format"], found["class"]), ("fpsdet.arena/1", "interactive_reference_demo"))
        self.assertEqual({row["id"] for row in found["scenarios"]}, set(REQUIRED))
        declared = harness().scenarios()
        for row in found["scenarios"]:
            with self.subTest(row["id"]):
                self.assertEqual(row["expected"], declared[row["id"]]["expected"])
                self.assertTrue(row["as_expected"], row["problems"])
                self.assertEqual(row["live_vs_offline"], "identical")
                self.assertEqual(row["secret_leaks"], [])
                self.assertEqual(row["personal_data"], [])
                self.assertEqual(row["case_problems"], {})
                self.assertEqual(row["observed"]["automated_action"], "none")
                self.assertIn(row["realization"]["status"], ("reproduced", "no_challenge"))
                self.assertEqual(row["timing"]["parse_errors"], 0)
                for case in row["cases"].values():
                    self.assertEqual(case["packet_recipe"], "fpsdet.packet/5")

    def test_the_result_was_made_by_this_arena_code(self):
        """Change the server, the client, the views or the harness, and the arena must be qualified again."""
        self.assertEqual(result()["code"], harness().code_identity())
        self.assertEqual(result()["profile"], harness().pilot.sha256_file(ARENA / "arena.json"))
        self.assertEqual(result()["engine"]["version"], harness().ENGINE_VERSION)
        self.assertIn("No person played", result()["no_person"])

    def test_the_committed_captures_reproduce_offline(self):
        self.assertEqual(harness().verify(), [])

    def test_normal_play_makes_no_case(self):
        row = scenario_result("normal_play")
        self.assertIn(row["observed"]["decision"], ("clean", "insufficient_data"))
        self.assertEqual(row["observed"]["kinds"], [])
        self.assertEqual(row["observed"]["eligibility"]["speed"], "eligible")
        self.assertEqual(row["observed"]["knowledge"], {"arena-bot-a": "known"})

    def test_the_gear_rules_fire_on_their_faults_as_fpsdet_defines_them(self):
        self.assertEqual(scenario_result("impossible_speed")["observed"]["kinds"], ["speed"])
        speed = scenario_result("impossible_speed")["observed"]["speed"]
        self.assertTrue(speed["sustained"])
        self.assertGreaterEqual(speed["longest_run"], 25)  # samples at 10 Hz: the current sample-count semantics
        self.assertEqual(scenario_result("fire_rate")["observed"]["kinds"], ["fire_rate"])
        self.assertEqual(scenario_result("fire_rate")["observed"]["eligibility"]["metronome"], "not_applicable")  # a held trigger paced by the server
        self.assertEqual(scenario_result("recoil_floor")["observed"]["kinds"], ["recoil_floor"])
        self.assertEqual(scenario_result("recoil_mirror")["observed"]["kinds"], ["mirror"])

    def test_knowledge_known_unknowable_and_unknown_each_do_what_the_engine_says(self):
        audible = scenario_result("audible_hidden_enemy")["observed"]
        self.assertEqual((audible["knowledge"]["arena-bot-b"], audible["eligibility"]["hidden"], audible["kinds"]), ("known", "eligible", []))
        quiet = scenario_result("unknowable_hidden_enemy")["observed"]
        self.assertEqual((quiet["eligibility"]["hidden"], quiet["kinds"]), ("eligible", []))
        self.assertTrue(quiet["knowledge_detail"]["arena-bot-b"]["statuses"].get("unknowable"))
        tracked = scenario_result("unknowable_hidden_tracked")["observed"]
        self.assertEqual((tracked["knowledge"]["arena-bot-b"], tracked["decision"], tracked["kinds"]), ("unknowable", "review", ["hidden"]))
        unchecked = scenario_result("unchecked_audio_channel")["observed"]
        self.assertEqual((unchecked["knowledge"]["arena-bot-b"], unchecked["eligibility"]["hidden"], unchecked["kinds"]), ("unknown", "telemetry_unavailable", []))
        # The same aim as the tracked scenario produced no evidence: the server sent no hidden time without its audio query.
        self.assertFalse(any("hidden_track_ms" in event for event in events("unchecked_audio_channel") if event["player_id"] == "arena-player"))
        self.assertTrue(all(event.get("audio_state") == "unchecked" for event in events("unchecked_audio_channel") if event["player_id"] == "arena-player" and "enemy_id" in event))

    def test_the_wire_check_fires_on_the_packet_reader_and_not_on_the_picture(self):
        self.assertEqual(scenario_result("wire_vs_picture")["observed"]["kinds"], ["wire"])
        self.assertEqual(scenario_result("normal_play")["observed"]["eligibility"]["wire"], "eligible")

    def test_the_active_challenge_binds_to_its_plan_and_needed_the_runtime_proof(self):
        row = scenario_result("active_challenge")
        challenge = row["observed"]["challenge"]
        self.assertEqual((challenge["status"], challenge["verification"]), ("followed", "per_sample"))
        self.assertEqual(challenge["verified_samples"], challenge["eligible_samples"])
        self.assertGreaterEqual(challenge["tracked_samples"], 8)
        self.assertGreaterEqual(challenge["total_ms"], 1200)
        case = row["cases"]["arena-player"]
        (bound,) = case["challenges"]
        plan = json.loads((ARENA / "captures" / "active_challenge" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(bound["challenge_id"], plan["challenges"][0]["challenge_id"])
        self.assertEqual(bound["plan"], plan["challenges"][0]["plan"])
        self.assertEqual(plan["version"], 2)
        self.assertEqual(case["inputs"]["recipe"], "fpsdet.player-events/3")
        self.assertEqual(row["realization"]["status"], "reproduced")
        named = [event for event in events("active_challenge") if event.get("challenge_id")]
        self.assertTrue(named)
        self.assertTrue(all(event["challenge_vision_state"] == "absent" and event["challenge_audio_state"] == "absent" for event in named))
        self.assertEqual(row["timing"]["verdicts"]["vision"], {"absent": row["timing"]["probe_ticks"]})
        self.assertGreater(row["client"]["probe_updates"], 0)  # the stock client was sent the probe as an ordinary body
        self.assertEqual(row["client"]["probe_sounds"], 0)  # and never a sound of it
        self.assertEqual(row["observed"]["label"], harness().EXPERIMENTAL_LABEL)

    def test_the_angle_hold_reproduces_the_known_weakness_and_is_labelled(self):
        row = scenario_result("angle_hold_false_positive")
        self.assertEqual(row["observed"]["decision"], "review")
        self.assertEqual(row["observed"]["kinds"], ["occluded_motion_replay"])
        self.assertEqual(row["observed"]["label"], harness().EXPERIMENTAL_LABEL)
        self.assertIn("angle_hold_false_positive", result()["experimental"]["applies_to"])
        self.assertIn("not production-qualified", result()["experimental"]["note"])
        self.assertEqual(harness().scenarios()["angle_hold_false_positive"]["standin"]["kind"], "holder")  # nobody followed anything
        challenge = row["observed"]["challenge"]
        self.assertEqual(challenge["verified_samples"], challenge["eligible_samples"])  # the body was unseen and unheard at every counted moment

    def test_external_evidence_makes_a_watch_at_most(self):
        row = scenario_result("external_record")
        fusion = row["observed"]["fusion"]
        self.assertEqual((fusion["decision"], fusion["rule"]), ("watch", "A"))
        self.assertIn(fusion["native_decision"], ("clean", "insufficient_data"))
        self.assertEqual(row["observed"]["kinds"], ["external_signal"])
        self.assertEqual(row["observed"]["automated_action"], "none")

    def test_an_external_record_cannot_create_a_review_and_a_review_stays_a_review(self):
        """The same records against two captures: a case with no native finding becomes a watch; a review is unchanged."""
        arena = harness()
        record = arena.external_record(arena.scenarios()["external_record"], "arena-impossible_speed-1")
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work) / "speed"
            folder.mkdir()
            (folder / "events.ndjson").write_bytes((ARENA / "captures" / "impossible_speed" / "events.ndjson").read_bytes())
            (folder / "external.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
            scored = arena.score_live(folder)
            case = scored["cases"]["arena-player"]
            self.assertEqual(case["decision"], "review")
            self.assertEqual(case["evidence"]["fusion"]["rule"], "C")
            self.assertEqual(sorted(obs["kind"] for obs in case["evidence"]["observations"]), ["external_signal", "speed"])

    def test_ai_off_and_on_give_the_same_decision_and_packet(self):
        arena = harness()
        folder = ARENA / "captures" / "unknowable_hidden_tracked"
        before = arena.score_live(folder)["cases"]["arena-player"]
        brief = arena.ai_brief(before, ["arena-player", "arena-bot-b"], transport=lambda body: "A plain-language brief. " + str(len(body["messages"])))
        self.assertTrue(brief.startswith("A plain-language brief."))
        after = arena.score_live(folder)["cases"]["arena-player"]
        self.assertEqual(before["evidence"]["packet"]["digest"], after["evidence"]["packet"]["digest"])
        self.assertEqual(before["decision"], after["decision"])
        self.assertNotIn("ai_brief", before["evidence"])

    def test_the_ai_brief_never_sees_raw_events_or_the_evidence_block(self):
        arena = harness()
        case = arena.score_live(ARENA / "captures" / "active_challenge")["cases"]["arena-player"]
        seen = {}
        arena.ai_brief(case, ["arena-player"], transport=lambda body: seen.update(body) or "ok")
        payload = json.loads(seen["messages"][1]["content"])
        self.assertNotIn("evidence", payload)
        self.assertEqual(payload["player_id"], "redacted")
        self.assertNotIn("arena-player", json.dumps(payload))

    def test_public_captures_carry_no_secret_realization_or_personal_data(self):
        arena = harness()
        for name in REQUIRED:
            folder = ARENA / "captures" / name
            files = sorted(folder.iterdir())
            self.assertEqual(arena.leak_scan(files, None, None), [], name)
            self.assertEqual(arena.personal_data(files), [], name)
            self.assertEqual({path.name for path in files} - {"events.ndjson", "plan.json", "external.ndjson"}, set(), name)
            for event in events(name):
                self.assertIn(event["player_id"], ("arena-player", "arena-bot-a", "arena-bot-b"))
                self.assertEqual(event["game_id"], "fpsdet-arena")

    def test_the_pilots_and_the_benchmark_are_untouched(self):
        """The arena consumes fpsdet. P12, P13 and Benchmark v1 keep their code identity and their results."""
        import importlib.util as util

        for name, folder, harness_file in (("pilot_for_arena_test", "pilot", "pilot.py"), ("study_for_arena_test", "human-pilot", "study.py")):
            spec = util.spec_from_file_location(name, ROOT / "examples" / folder / harness_file)
            module = util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            if folder == "pilot":
                self.assertEqual(module.code_identity(), json.loads((ROOT / "examples" / "pilot" / "result.json").read_text(encoding="utf-8"))["code"])
            else:
                dry = json.loads((ROOT / "examples" / "human-pilot" / "dry-run" / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(dry["kind"], "machine_standin")
        from fpsdet import benchmark as bm

        self.assertNotIn("interactive_reference_demo", bm.CLASSES)  # the arena is not a benchmark class
        release = json.loads((ROOT / "benchmark" / "release.json").read_text(encoding="utf-8"))
        self.assertIn("digest", release)


if __name__ == "__main__":
    unittest.main()
