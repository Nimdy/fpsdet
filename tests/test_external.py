"""External evidence: records from other integrity systems, kept apart from fpsdet's own evidence.

NativeFusionSurfacesTest was written before external evidence (P5, phase E0) to pin the places it could
reach: what the AI brief is sent, how a batch watch moves a decision, that reports move nothing, what
packet/1 binds, and that a packet written before P5 verifies.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import copy
import hashlib
import tempfile

import fpsdet.external as external
from fpsdet.ai_triage import triage_case
from fpsdet.external import ExternalError, adapter_from_dict, build_record, load_adapter, native_record, read_external
from fpsdet.models import Case, Event, GameProfile
from fpsdet.persist import case_to_dict
from fpsdet.pipeline import run_score
from fpsdet.priority import review_order
from fpsdet.provenance import PACKET_PROVENANCE, packet_material, verify_packet
from fpsdet.synthetic import build_demo

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "external"
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


def native(**values) -> dict:
    record = {"format": "fpsdet.external/1", "provider": "example-integrity", "source_class": "client_integrity",
              "kind": "memory_integrity_anomaly", "direction": "adverse", "subject_id": "x", "match_id": "m1"}
    record.update(values)
    return {key: value for key, value in record.items() if value is not None}


def write_lines(folder: Path, name: str, rows) -> Path:
    path = folder / name
    path.write_text("".join((row if isinstance(row, str) else json.dumps(row)) + "\n" for row in rows), encoding="utf-8")
    return path


class ExternalRecordTest(unittest.TestCase):
    """A provider's claim, kept as the provider made it."""

    def test_every_claim_is_kept_verbatim(self):
        record = native_record(native(kind="Memory Integrity Anomaly ✓", observed_at="2026-09-30T12:00:00Z", provider_record_id="EI-1",
                                      confidence=0.82, confidence_scale="probability", confidence_meaning="the vendor's calibrated probability",
                                      telemetry_domain="endpoint_memory", metadata={"module": "overlay-x", "build": 142, "signed": False}))
        self.assertEqual(record.kind, "Memory Integrity Anomaly ✓")
        self.assertEqual((record.confidence, record.confidence_scale, record.confidence_meaning), (0.82, "probability", "the vendor's calibrated probability"))
        self.assertEqual(record.observed_at, "2026-09-30T12:00:00Z")
        self.assertEqual(record.metadata, (("build", 142), ("module", "overlay-x"), ("signed", False)))
        self.assertEqual(record.provider_group, "example-integrity")  # a provider is its own group unless it says otherwise
        self.assertTrue(record.external_id.startswith("ext-"))

    def test_confidence_is_never_reinterpreted(self):
        cases = {
            "a score stays a score": (dict(confidence=87, confidence_scale="score"), (87, "score")),
            "a label stays a label": (dict(confidence="high", confidence_scale="label"), ("high", "label")),
            "no scale is unspecified": (dict(confidence=0.82), (0.82, "unspecified")),
            "no confidence": ({}, (None, "unspecified")),
        }
        for name, (values, expected) in cases.items():
            with self.subTest(name):
                record = native_record(native(**values))
                self.assertEqual((record.confidence, record.confidence_scale), expected)
                self.assertIsInstance(record.confidence, type(expected[0]))
        malformed = {
            "a probability above 1": dict(confidence=82, confidence_scale="probability"),
            "a word as a probability": dict(confidence="high", confidence_scale="probability"),
            "a number as a label": dict(confidence=3, confidence_scale="label"),
            "true as a confidence": dict(confidence=True),
            "an object as a confidence": dict(confidence={"value": 1}),
            "a list as a confidence": dict(confidence=[0.8]),
            "an unknown scale": dict(confidence=0.8, confidence_scale="percent"),
            "a meaning with no confidence": dict(confidence_meaning="high is bad"),
            "an enormous number": dict(confidence=1e300),
        }
        for name, values in malformed.items():
            with self.subTest(name), self.assertRaises(ExternalError):
                native_record(native(**values))

    def test_unknown_fields_and_values_are_refused(self):
        bad = {
            "a field the format does not define": dict(confidance=0.8),
            "an unknown source class": dict(source_class="anticheat"),
            "an unknown direction": dict(direction="guilty"),
            "an unknown telemetry domain": dict(telemetry_domain="vibes"),
            "match time with no match": dict(match_id=None, started_ms=10),
            "a window that ends before it starts": dict(started_ms=500, ended_ms=100),
            "a negative time": dict(started_ms=-1),
            "a date that does not exist": dict(observed_at="2026-02-30"),
            "a date in another shape": dict(observed_at="30/09/2026"),
            "a control character": dict(kind="anomaly\x1b[31m"),
            "a direction override": dict(kind="anomaly\u202egnp.exe"),
            "an upper-case provider": dict(provider="Example"),
            "a provider with a space": dict(provider="example integrity"),
            "an empty subject": dict(subject_id=""),
            "a missing kind": dict(kind=None),
            "the wrong format": dict(format="fpsdet.external/2"),
        }
        for name, values in bad.items():
            with self.subTest(name), self.assertRaises(ExternalError):
                native_record(native(**values))

    def test_identity_is_the_claim_and_nothing_else(self):
        first = native_record(native(confidence=87, confidence_scale="score", metadata={"a": 1, "b": "two"}))
        reordered = json.loads(json.dumps(dict(reversed(list(native(confidence=87, confidence_scale="score", metadata={"b": "two", "a": 1}).items())))))
        self.assertEqual(native_record(reordered).external_id, first.external_id)
        changes = {
            "provider": dict(provider="other-vendor"), "group": dict(provider_group="parent"), "class": dict(source_class="custom_detector"),
            "domain": dict(telemetry_domain="endpoint_memory"), "kind": dict(kind="other"), "direction": dict(direction="context"),
            "subject": dict(subject_id="y"), "match": dict(match_id="m2"), "window": dict(started_ms=1), "time": dict(observed_at="2026-01-01"),
            "value": dict(confidence=88), "an int and a float": dict(confidence=87.0), "scale": dict(confidence_scale="unspecified"),
            "meaning": dict(confidence_meaning="m"), "record id": dict(provider_record_id="r"), "metadata": dict(metadata={"a": 2, "b": "two"}),
        }
        for name, values in changes.items():
            with self.subTest(name):
                base = dict(confidence=87, confidence_scale="score", metadata={"a": 1, "b": "two"})
                self.assertNotEqual(native_record(native(**{**base, **values})).external_id, first.external_id)
        material = json.dumps(first.material(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(first.external_id, "ext-" + hashlib.sha256(b"fpsdet.external/1\0" + material.encode()).hexdigest()[:24])


ADAPTER = json.loads((EXAMPLES / "example-integrity.adapter.json").read_text())


class AdapterTest(unittest.TestCase):
    """Adapters are data: key lookups, constants and an allowlist. Nothing in one can run."""

    def test_the_example_adapter_maps_a_provider_record(self):
        adapter = load_adapter(EXAMPLES / "example-integrity.adapter.json")
        raw = json.loads((EXAMPLES / "example-integrity.ndjson").read_text().splitlines()[0])
        record = adapter.map(raw)
        self.assertEqual((record.provider, record.source_class, record.telemetry_domain), ("example-integrity", "client_integrity", "endpoint_memory"))
        self.assertEqual((record.subject_id, record.match_id, record.started_ms, record.ended_ms), ("adrenaline", "m1", 1000, 9000))
        self.assertEqual((record.kind, record.direction, record.confidence, record.confidence_scale), ("memory_integrity_anomaly", "adverse", 87, "score"))
        self.assertIn("not a probability", record.confidence_meaning)
        self.assertEqual(dict(record.metadata), {"module": "overlay-x", "client_build": "1.4.2"})
        self.assertEqual(adapter.digest, "sha256:" + hashlib.sha256(b"fpsdet.external-adapter/1\0" + json.dumps(ADAPTER, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())

    def test_the_same_claim_through_an_adapter_or_natively_is_one_record(self):
        mapped = load_adapter(EXAMPLES / "example-integrity.adapter.json").map(json.loads((EXAMPLES / "example-integrity.ndjson").read_text().splitlines()[0]))
        values = {**mapped.material()}
        scope, confidence = values.pop("scope"), values.pop("confidence")
        direct = native_record({"format": "fpsdet.external/1", **values, **scope, "confidence": confidence["value"],
                                "confidence_scale": confidence["scale"], "confidence_meaning": confidence["meaning"]})
        self.assertEqual(direct.external_id, mapped.external_id)

    def test_a_provider_record_cannot_claim_another_provider_or_class(self):
        adapter = adapter_from_dict(ADAPTER)
        raw = {"player": {"pseudonym": "x"}, "detection": {"type": "memory_integrity_anomaly"}, "provider": "someone-else",
               "source_class": "human_review", "direction": "favorable"}
        record = adapter.map(raw)
        self.assertEqual((record.provider, record.source_class, record.direction), ("example-integrity", "client_integrity", "adverse"))

    def test_an_adapter_cannot_do_anything_but_map(self):
        def broken(**changes) -> dict:
            adapter = copy.deepcopy(ADAPTER)
            for key, value in changes.items():
                if value is None:
                    adapter.pop(key, None)
                else:
                    adapter[key] = value
            return adapter

        refused = {
            "a code key": broken(code="import os"),
            "an expression path": broken(fields={**ADAPTER["fields"], "kind": "__import__('os').system('x')"}),
            "an index path": broken(fields={**ADAPTER["fields"], "kind": "detection.types[0]"}),
            "an empty path step": broken(fields={**ADAPTER["fields"], "kind": "detection..type"}),
            "a template": broken(fields={**ADAPTER["fields"], "kind": "{{detection.type}}"}),
            "a path too deep": broken(fields={**ADAPTER["fields"], "kind": "a.b.c.d.e.f.g"}),
            "mapping the provider": broken(fields={**ADAPTER["fields"], "provider": "vendor"}),
            "mapping the source class": broken(fields={**ADAPTER["fields"], "source_class": "class"}),
            "a constant subject": broken(constants={"subject_id": "everyone"}),
            "a field both mapped and constant": broken(constants={**ADAPTER["constants"], "kind": "x"}),
            "no direction at all": broken(directions=None),
            "direction two ways": broken(constants={**ADAPTER["constants"], "direction": "adverse"}),
            "an unknown direction in the table": broken(directions={"memory_integrity_anomaly": "guilty"}),
            "an unknown source class": broken(source_class="anticheat"),
            "no subject mapping": broken(fields={key: value for key, value in ADAPTER["fields"].items() if key != "subject_id"}),
            "a bad metadata key": broken(metadata={"bad key": "a.b"}),
            "the wrong format": broken(format="fpsdet.adapter/9"),
            "a string version": broken(version="1"),
        }
        for name, adapter in refused.items():
            with self.subTest(name), self.assertRaises(ExternalError):
                adapter_from_dict(adapter)
        with tempfile.TemporaryDirectory() as folder:
            big = Path(folder) / "big.json"
            big.write_text(json.dumps({**ADAPTER, "directions": {f"k{i}" * 40: "adverse" for i in range(250)}}))
            with self.assertRaises(ExternalError):
                load_adapter(big)

    def test_a_kind_the_adapter_does_not_know_is_refused(self):
        with self.assertRaises(ExternalError):
            adapter_from_dict(ADAPTER).map({"player": {"pseudonym": "x"}, "detection": {"type": "new_thing"}})


class BoundsTest(unittest.TestCase):
    """A hostile external file is skipped line by line, and never quoted back."""

    def read(self, rows, adapter=None, **options):
        with tempfile.TemporaryDirectory() as folder:
            path = write_lines(Path(folder), "records.ndjson", rows)
            return read_external([(path, adapter)], **options)

    def test_each_kind_of_bad_line_is_skipped_and_named(self):
        secret = "PAYLOAD-7Q"
        rows = [
            native(kind="ok-1"),
            "{" * 20 + "}" * 20,  # too deep
            '{"format": "fpsdet.external/1", "kind": "a", "kind": "b"}',  # a repeated key
            json.dumps(native(kind="ok-2"))[:-1] + ', "confidence": NaN}',
            "x" * (external.MAX_LINE_BYTES + 10),
            native(kind="k" * 200 + secret),
            native(metadata={f"k{i}": i for i in range(40)}),
            native(metadata={"blob": secret + "x" * 300}),
            native(metadata={"nested": {"a": secret}}),
            "not json " + secret,
            native(kind="ok-3"),
        ]
        out = self.read(rows)
        self.assertEqual(sorted(record.kind for record in out.records.values()), ["ok-1", "ok-3"])
        self.assertEqual(len(out.errors), 9)
        self.assertEqual(out.sources[0].errors, 9)
        self.assertTrue(all("line" in error for error in out.errors))
        self.assertFalse(any(secret in error for error in out.errors))

    def test_strict_mode_stops_at_the_first_bad_line(self):
        with self.assertRaises(ExternalError):
            self.read([native(), "nope"], strict=True)

    def test_a_line_too_long_is_read_past_in_pieces(self):
        out = self.read(["y" * (external.MAX_LINE_BYTES * 5), native(kind="after")])
        self.assertEqual([record.kind for record in out.records.values()], ["after"])

    def test_a_file_too_large_is_not_read(self):
        original = external.MAX_FILE_BYTES
        external.MAX_FILE_BYTES = 100
        try:
            out = self.read([native(), native(kind="two")])
        finally:
            external.MAX_FILE_BYTES = original
        self.assertEqual((len(out.records), len(out.errors)), (0, 1))

    def test_one_player_cannot_flood_the_run(self):
        out = self.read([native(kind=f"k{i}") for i in range(external.MAX_RECORDS_PER_SUBJECT + 5)])
        self.assertEqual(len(out.for_subject("x")), external.MAX_RECORDS_PER_SUBJECT)
        self.assertEqual(len(out.errors), 5)

    def test_bytes_that_are_not_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.ndjson"
            path.write_bytes(b'{"format": "\xff"}\n' + json.dumps(native()).encode() + b"\n")
            out = read_external([(path, None)])
        self.assertEqual((len(out.records), len(out.errors)), (1, 1))


class LoadTest(unittest.TestCase):
    def test_duplicates_count_once_across_files(self):
        adapter = load_adapter(EXAMPLES / "example-integrity.adapter.json")
        path = EXAMPLES / "example-integrity.ndjson"
        out = read_external([(path, adapter), (path, adapter)])
        first, second = out.sources
        self.assertEqual((first.records, first.duplicates), (12, 1))
        self.assertEqual((second.records, second.duplicates), (0, 13))
        self.assertEqual(first.artifact, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(first.adapter, adapter.digest)
        self.assertEqual(len(out.for_subject("blasted")), 2)

    def test_a_players_records_are_in_id_order_not_file_order(self):
        rows = [native(kind=f"k{i}") for i in range(6)]
        forward = BoundsTest().read(rows)
        backward = BoundsTest().read(list(reversed(rows)))
        self.assertEqual([r.external_id for r in forward.for_subject("x")], [r.external_id for r in backward.for_subject("x")])
        self.assertEqual(sorted(r.external_id for r in forward.for_subject("x")), [r.external_id for r in forward.for_subject("x")])

    def test_the_examples_load_cleanly(self):
        out = read_external([(EXAMPLES / "native.ndjson", None)] + [
            (EXAMPLES / f"{name}.ndjson", load_adapter(EXAMPLES / f"{name}.adapter.json"))
            for name in ("example-integrity", "example-account-status", "example-league-admin")
        ])
        self.assertEqual(out.errors, [])
        self.assertEqual(len(out.records), 20)
        classes = {record.source_class for record in out.records.values()}
        self.assertEqual(classes, {"client_integrity", "platform_attestation", "account_status", "tournament_finding", "human_review", "custom_detector"})


if __name__ == "__main__":
    unittest.main()
