"""Authenticated external evidence: which provider's key signed a record, and nothing more.

CurrentTrustModelTest was written before authentication (P7, phase A0) to pin what fpsdet trusted about an
external record: nothing. Any line naming any provider was that provider's claim.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fpsdet.external import read_external
from fpsdet.provenance import PACKET_V2_PROVENANCE

from tests.test_external import FUSED, native, scored, write_lines


def external_obs(case: dict) -> list[dict]:
    return [obs for obs in case["evidence"]["observations"] if obs["family"] == "external"]


def run(rows) -> dict[str, dict]:
    with tempfile.TemporaryDirectory() as folder:
        loaded = read_external([(write_lines(Path(folder), "records.ndjson", rows), None)])
    return scored(loaded)


class CurrentTrustModelTest(unittest.TestCase):
    """What an external record could claim before signatures, and what fpsdet took on trust."""

    def test_every_external_observation_is_unverified(self):
        rows = [obs for case in FUSED.values() for obs in external_obs(case)]
        self.assertEqual(len(rows), 19)  # 20 example records; one is about a player with no case
        # P7 replaced "unverified" with explicit states. These records carry no signature: "unsigned".
        self.assertEqual({json.dumps(obs["evidence"]["authenticity"]) for obs in rows}, {'{"status": "unsigned"}'})

    def test_a_line_naming_any_provider_is_that_providers_claim(self):
        # Anyone who can write to the external file can make a watch for a player in a scored match. Still true
        # for an unsigned record under the default policy; it now says so, as "unsigned".
        cases = run([native(subject_id="adrenaline", provider="example-integrity", provider_record_id="forged-1")])
        self.assertEqual(cases["adrenaline"]["decision"], "watch")
        (obs,) = external_obs(cases["adrenaline"])
        self.assertEqual((obs["evidence"]["provider"], obs["evidence"]["authenticity"]), ("example-integrity", {"status": "unsigned"}))

    def test_a_record_chooses_its_own_provider_group(self):
        cases = run([native(subject_id="adrenaline", provider="someone-else", provider_group="example-integrity")])
        (obs,) = external_obs(cases["adrenaline"])
        self.assertEqual(obs["evidence"]["provider_group"], "example-integrity")

    def test_a_signature_field_is_refused_not_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            loaded = read_external([(write_lines(Path(folder), "r.ndjson", [native(signature={"value": "x"})]), None)])
        self.assertEqual((len(loaded.records), len(loaded.errors)), (0, 1))

    def test_the_graph_has_no_notion_of_a_key(self):
        from fpsdet.graph import NODE_TYPES_V1, RELATIONS_V1

        # graph/1 had none. P7 added graph/2, with provider keys; graph/1 is unchanged and still read.
        self.assertNotIn("provider_key", NODE_TYPES_V1)
        self.assertEqual(sorted(RELATIONS_V1), sorted([
            "about", "occurred_in", "supports", "depends_on", "derived_from", "names_partner", "provided_by",
            "uses_telemetry_domain", "compared_against", "uses_history",
        ]))
        graph = json.loads((Path(__file__).resolve().parent / "fixtures" / "historical-packets-p6.json").read_text())["cases"][0]["evidence"]["graph"]
        records = [node for node in graph["nodes"] if node["type"] == "external_record"]
        self.assertEqual({tuple(sorted(node["attributes"])) for node in records}, {("direction", "provider", "source_class")})
        self.assertEqual(graph["recipe"], "fpsdet.graph/1")

    def test_packet_3_binds_these_external_fields(self):
        self.assertEqual(PACKET_V2_PROVENANCE["external"], ("mode", "recipe", "digest", "records", "sources"))
        case = FUSED["adrenaline"]
        # P7 added the registry and the policy to provenance.external, and each source's signature counts.
        self.assertEqual(sorted(case["evidence"]["provenance"]["external"]), ["digest", "mode", "policy", "recipe", "records", "registry", "sources"])
        self.assertEqual(sorted(case["evidence"]["provenance"]["external"]["sources"][0]), [
            "adapter", "adapter_name", "artifact", "authentication", "duplicates", "errors", "excluded", "records",
        ])

    def test_identity_is_the_claim(self):
        first = run([native(subject_id="adrenaline", provider_record_id="r-1")])["adrenaline"]
        again = run([native(subject_id="adrenaline", provider_record_id="r-1"), native(subject_id="adrenaline", provider_record_id="r-1")])["adrenaline"]
        self.assertEqual(external_obs(first)[0]["evidence"]["external_id"], external_obs(again)[0]["evidence"]["external_id"])
        self.assertEqual(len(external_obs(again)), 1)


HAVE_CRYPTO = importlib.util.find_spec("cryptography") is not None
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "external"
TEST_KEYS = json.loads((FIXTURES / "auth-test-keys.json").read_text())  # public test keys, committed on purpose
SEEDS = {key["key_id"]: bytes.fromhex(key["private_seed"]) for key in TEST_KEYS["keys"]}
REGISTRY_JSON = json.loads((EXAMPLES / "registry.json").read_text())
CLAIM = {"format": "fpsdet.external/1", "provider": "example-integrity", "source_class": "client_integrity", "kind": "memory_integrity_anomaly",
         "direction": "adverse", "subject_id": "adrenaline", "match_id": "m1", "confidence": 87, "confidence_scale": "score", "provider_record_id": "EI-9001"}


def registry(rows=None):
    from fpsdet.auth import Registry

    return Registry.from_dict(rows or REGISTRY_JSON)


@unittest.skipUnless(HAVE_CRYPTO, "signature checking needs the optional cryptography package")
class RegistryTest(unittest.TestCase):
    """The operator's trusted keys: public keys only, strictly read, with an identity that ignores order."""

    def test_the_example_registry(self):
        reg = registry()
        self.assertEqual(len(reg), 3)
        self.assertEqual([key.status for key in reg.keys()], ["revoked", "retired", "active"])  # by key id: 2024-11, 2025-07, 2026-01
        self.assertEqual(reg.digest, "sha256:f5151fe1e0e95f26ac8b618ac0431015b94f37a87868711dd0c9c0f791887017")
        self.assertEqual(reg.summary(), {"recipe": "fpsdet.provider-registry/1", "digest": reg.digest, "keys": 3})

    def test_12_order_does_not_move_the_digest(self):
        rows = copy.deepcopy(REGISTRY_JSON)
        keys = rows["providers"]["example-integrity"]["keys"]
        for seed in range(5):
            random.Random(seed).shuffle(keys)
            rows["providers"] = {"other-vendor": {"keys": [dict(keys[0], key_id="k1")]}, "example-integrity": {"keys": keys}}
            reordered = {"format": rows["format"], "providers": dict(reversed(list(rows["providers"].items())))}
            self.assertEqual(registry(rows).digest, registry(reordered).digest)

    def test_13_a_change_in_trust_moves_the_digest(self):
        base = registry().digest

        def changed(edit):
            rows = copy.deepcopy(REGISTRY_JSON)
            edit(rows["providers"]["example-integrity"]["keys"])
            return registry(rows).digest

        self.assertNotEqual(changed(lambda keys: keys[0].update(status="revoked")), base)
        self.assertNotEqual(changed(lambda keys: keys.pop()), base)
        self.assertNotEqual(changed(lambda keys: keys[0].update(key_id="2026-02")), base)
        self.assertNotEqual(changed(lambda keys: keys[0].update(public_key=keys[1]["public_key"])), base)

    def test_9_a_malformed_registry_is_refused(self):
        from fpsdet.auth import AuthError

        def broken(edit):
            rows = copy.deepcopy(REGISTRY_JSON)
            edit(rows)
            return rows

        key = lambda rows: rows["providers"]["example-integrity"]["keys"][0]
        refused = {
            "a short public key": broken(lambda r: key(r).update(public_key=base64.b64encode(b"x" * 31).decode())),
            "a public key that is not base64": broken(lambda r: key(r).update(public_key="not base64!")),
            "a private key": broken(lambda r: key(r).update(private_key="AAAA")),
            "a seed": broken(lambda r: key(r).update(seed="AAAA")),
            "an unknown algorithm": broken(lambda r: key(r).update(algorithm="rsa-2048")),
            "an unknown status": broken(lambda r: key(r).update(status="trusted")),
            "an extra field": broken(lambda r: key(r).update(note="hi")),
            "a key listed twice": broken(lambda r: r["providers"]["example-integrity"]["keys"].append(dict(key(r)))),
            "an upper-case provider": broken(lambda r: r["providers"].update({"Example": r["providers"]["example-integrity"]})),
            "a provider with no keys": broken(lambda r: r["providers"].update({"empty": {"keys": []}})),
            "the wrong format": broken(lambda r: r.update(format="fpsdet.provider-registry/9")),
            "an extra top-level field": broken(lambda r: r.update(trust_everyone=True)),
        }
        for name, rows in refused.items():
            with self.subTest(name), self.assertRaises(AuthError):
                registry(rows)

    def test_without_the_package_a_registry_cannot_be_used_and_says_why(self):
        from fpsdet import auth

        with mock.patch.object(auth, "_ed25519", side_effect=auth.AuthUnavailable("needs cryptography")):
            with self.assertRaises(auth.AuthUnavailable):
                registry()


@unittest.skipUnless(HAVE_CRYPTO, "signature checking needs the optional cryptography package")
class SignatureTest(unittest.TestCase):
    """Ed25519 over a domain-separated canonical claim. verified means the registered key signed it."""

    def check(self, envelope, provider="example-integrity", reg="default"):
        from fpsdet.auth import parse_envelope, verify

        claim, signature = parse_envelope(envelope)
        return verify(claim, signature, provider, registry() if reg == "default" else reg)

    def signed(self, key_id="2026-01", claim=None, seed=None, label=None):
        from fpsdet.auth import sign

        return sign(claim or CLAIM, seed or SEEDS[key_id], "example-integrity", label or key_id)

    def test_1_a_valid_signature_is_verified_and_pinned(self):
        from fpsdet.auth import signed_bytes

        envelope = self.signed()
        self.assertEqual(envelope["signature"]["value"], "wEmEVmBhuNagQUuL5eem9OkLZvhOVLc2qrCn70mUM6FCTDKk8LIsmF+QLgNq+dJX9tky57ryCaJ6b8djvTcSDw==")
        self.assertEqual(hashlib.sha256(signed_bytes(CLAIM)).hexdigest(), "4fde5706220e0c97cb93862289b30905907db388b8d6a45decebb72441859821")
        self.assertEqual(signed_bytes(CLAIM), b"fpsdet.external-signature/1\0" + json.dumps(CLAIM, sort_keys=True, separators=(",", ":")).encode())
        result = self.check(envelope)
        self.assertEqual(result.to_dict(), {"status": "verified", "algorithm": "ed25519", "provider": "example-integrity", "key_id": "2026-01",
                                            "key_status": "active", "registry": registry().digest})

    def test_the_claims_key_order_does_not_matter(self):
        envelope = self.signed()
        envelope["claim"] = dict(reversed(list(envelope["claim"].items())))
        self.assertEqual(self.check(envelope).status, "verified")

    def test_2_one_changed_value_or_byte_is_invalid(self):
        changed = self.signed()
        changed["claim"]["subject_id"] = "someone-else"
        self.assertEqual(self.check(changed).status, "invalid")
        flipped = self.signed()
        raw = bytearray(base64.b64decode(flipped["signature"]["value"]))
        raw[10] ^= 1
        flipped["signature"]["value"] = base64.b64encode(bytes(raw)).decode()
        self.assertEqual(self.check(flipped).status, "invalid")

    def test_3_a_signature_naming_another_provider_is_invalid(self):
        self.assertEqual(self.check(self.signed(), provider="someone-else").status, "invalid")

    def test_4_the_wrong_key_is_invalid(self):
        self.assertEqual(self.check(self.signed(seed=SEEDS["2025-07"], label="2026-01")).status, "invalid")

    def test_5_an_unknown_key_is_unknown(self):
        result = self.check(self.signed(label="2027-01"))
        self.assertEqual((result.status, result.key_id), ("unknown_key", "2027-01"))

    def test_6_a_revoked_key_is_never_verified(self):
        self.assertEqual(self.check(self.signed("2024-11")).status, "revoked_key")
        tampered = self.signed("2024-11")
        tampered["claim"]["kind"] = "other"
        self.assertEqual(self.check(tampered).status, "invalid")

    def test_11_a_retired_key_still_verifies_what_it_signed(self):
        result = self.check(self.signed("2025-07"))
        self.assertEqual((result.status, result.key_status), ("verified", "retired"))

    def test_7_an_unsupported_algorithm(self):
        envelope = self.signed()
        envelope["signature"]["algorithm"] = "rsa-pss"
        self.assertEqual(self.check(envelope).status, "unsupported_algorithm")

    def test_8_and_14_a_malformed_signature_or_a_record_bringing_its_own_key(self):
        from fpsdet.auth import AuthError, parse_envelope

        def damaged(edit):
            envelope = self.signed()
            edit(envelope)
            return envelope

        refused = {
            "not base64": damaged(lambda e: e["signature"].update(value="!!")),
            "the wrong length": damaged(lambda e: e["signature"].update(value=base64.b64encode(b"x" * 63).decode())),
            "its own public key": damaged(lambda e: e["signature"].update(public_key=REGISTRY_JSON["providers"]["example-integrity"]["keys"][0]["public_key"])),
            "a key on the envelope": damaged(lambda e: e.update(key={"public_key": "x"})),
            "no key id": damaged(lambda e: e["signature"].pop("key_id")),
            "a claim that is not an object": damaged(lambda e: e.update(claim="text")),
            "a malformed provider": damaged(lambda e: e["signature"].update(provider="Example Integrity")),
        }
        for name, envelope in refused.items():
            with self.subTest(name), self.assertRaises(AuthError):
                parse_envelope(envelope)

    def test_without_a_registry_a_signature_is_not_checked(self):
        result = self.check(self.signed(), reg=None)
        self.assertEqual((result.status, result.key_id, result.registry), ("not_checked", "2026-01", None))

    def test_the_signature_is_domain_separated(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        bare = Ed25519PrivateKey.from_private_bytes(SEEDS["2026-01"]).sign(json.dumps(CLAIM, sort_keys=True, separators=(",", ":")).encode())
        envelope = self.signed()
        envelope["signature"]["value"] = base64.b64encode(bare).decode()
        self.assertEqual(self.check(envelope).status, "invalid")

    def test_verified_states_are_exactly_these(self):
        from fpsdet.auth import REJECTED, STATUSES, UNAUTHENTICATED, VERIFIED

        self.assertEqual(set(STATUSES), REJECTED | UNAUTHENTICATED | {VERIFIED})
        self.assertEqual(REJECTED & UNAUTHENTICATED, set())


def envelope(claim: dict, key_id: str = "2026-01", *, seed=None, label=None, provider="example-integrity") -> dict:
    from fpsdet.auth import sign

    return sign(claim, seed or SEEDS[key_id], provider, label or key_id)


def read(*sources, registry_rows="default", **options):
    """Read sources: each (rows, adapter or None). Written to temporary files in order."""
    with tempfile.TemporaryDirectory() as folder:
        paths = [(write_lines(Path(folder), f"r{index}.ndjson", rows), adapter) for index, (rows, adapter) in enumerate(sources)]
        reg = None if registry_rows is None else registry(REGISTRY_JSON if registry_rows == "default" else registry_rows)
        return read_external(paths, registry=reg, **options)


def integrity_adapter():
    from fpsdet.external import load_adapter

    return load_adapter(EXAMPLES / "example-integrity.adapter.json")


def integrity_rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (EXAMPLES / name).read_text().splitlines()]


@unittest.skipUnless(HAVE_CRYPTO, "signature checking needs the optional cryptography package")
class AuthenticatedRecordsTest(unittest.TestCase):
    """Signatures checked while reading: before mapping, as the provider the record is read as."""

    def test_the_signed_examples_verify_and_dedupe_with_their_unsigned_claims(self):
        signed, unsigned = integrity_rows("example-integrity-signed.ndjson"), integrity_rows("example-integrity.ndjson")
        for order in ((signed, unsigned), (unsigned, signed)):
            loaded = read(*[(rows, integrity_adapter()) for rows in order])
            (adrenaline,) = loaded.for_subject("adrenaline")
            self.assertEqual((adrenaline.auth.status, adrenaline.auth.key_id), ("verified", "2026-01"))
            self.assertEqual(adrenaline.external_id, integrity_adapter().map(unsigned[0]).external_id)  # the claim's identity
            self.assertEqual(loaded.for_subject("weak-human")[0].auth.key_status, "retired")
            self.assertEqual(len(loaded.records), 14)  # 12 unsigned claims and 2 new signed ones; the shared claim counts once

    def test_a_tampered_claim_is_refused_and_makes_no_watch(self):
        bad = envelope(native(subject_id="adrenaline", provider_record_id="t-1"))
        bad["claim"]["kind"] = "something_else"
        loaded = read(([bad, native(subject_id="glitch")], None))
        self.assertEqual((len(loaded.records), loaded.sources[0].errors, loaded.sources[0].authentication), (1, 1, {"invalid": 1, "unsigned": 1}))
        self.assertIn("signature invalid", loaded.errors[0])
        self.assertEqual(scored(loaded)["adrenaline"]["decision"], "clean")

    def test_a_revoked_key_and_an_unsupported_algorithm_are_refused(self):
        revoked = envelope(native(subject_id="adrenaline"), "2024-11")
        odd = envelope(native(subject_id="glitch"))
        odd["signature"]["algorithm"] = "rsa-pss"
        loaded = read(([revoked, odd], None))
        self.assertEqual((len(loaded.records), loaded.sources[0].authentication), (0, {"revoked_key": 1, "unsupported_algorithm": 1}))

    def test_an_unknown_key_is_read_but_never_authenticated(self):
        loaded = read(([envelope(native(subject_id="adrenaline"), label="2027-01")], None))
        (record,) = loaded.for_subject("adrenaline")
        self.assertEqual(record.auth.status, "unknown_key")
        cases = scored(loaded)
        self.assertEqual(cases["adrenaline"]["decision"], "watch")  # as any unauthenticated record, by the default policy
        self.assertEqual(external_obs(cases["adrenaline"])[0]["evidence"]["authenticity"]["status"], "unknown_key")

    def test_a_provider_name_alone_is_not_verification(self):
        loaded = read(([native(subject_id="adrenaline", provider="example-integrity")], None))
        self.assertEqual(loaded.for_subject("adrenaline")[0].auth.status, "unsigned")

    def test_15_an_adapter_cannot_carry_a_signature_across_providers_or_past_a_change(self):
        from fpsdet.external import adapter_from_dict

        raw = integrity_rows("example-integrity.ndjson")[0]
        other = adapter_from_dict({**json.loads((EXAMPLES / "example-integrity.adapter.json").read_text()), "provider": "another-vendor"})
        signed = envelope(raw)
        loaded = read(([signed], other))
        self.assertEqual((len(loaded.records), loaded.sources[0].authentication), (0, {"invalid": 1}))
        changed = envelope(raw)
        changed["claim"]["detection"]["score"] = 99
        loaded = read(([changed], integrity_adapter()))
        self.assertEqual(loaded.sources[0].authentication, {"invalid": 1})

    def test_14_a_record_cannot_vouch_for_itself(self):
        claims_verified = native(subject_id="adrenaline")
        claims_verified["authenticity"] = "verified"
        with_key = envelope(native(subject_id="glitch"))
        with_key["signature"]["public_key"] = REGISTRY_JSON["providers"]["example-integrity"]["keys"][0]["public_key"]
        loaded = read(([claims_verified, with_key], None))
        self.assertEqual((len(loaded.records), len(loaded.errors)), (0, 2))

    def test_verified_means_signed_not_true(self):
        from fpsdet.models import GameProfile

        rows = [
            envelope(native(subject_id="adrenaline")),  # adverse
            envelope(native(subject_id="steady-hands", direction="favorable", kind="integrity_ok")),
            envelope(native(subject_id="legal-heavy", source_class="account_status", kind="game_ban", match_id=None)),
            envelope(native(subject_id="listened", source_class="tournament_finding", kind="confirmed_cheating")),
        ]
        cases = scored(read((rows, None)))
        self.assertEqual({pid: cases[pid]["decision"] for pid in ("adrenaline", "steady-hands", "legal-heavy", "listened")},
                         {"adrenaline": "watch", "steady-hands": "clean", "legal-heavy": "clean", "listened": "watch"})
        for pid in ("adrenaline", "steady-hands", "legal-heavy", "listened"):
            (obs,) = external_obs(cases[pid])
            self.assertEqual(obs["evidence"]["authenticity"]["status"], "verified")
        self.assertEqual(external_obs(cases["steady-hands"])[0]["evidence"]["direction"], "favorable")
        self.assertEqual(external_obs(cases["legal-heavy"])[0]["evidence"]["context_because"], "account_status")
        self.assertEqual({pid for pid, case in cases.items() if case["decision"] == "review"},
                         {pid for pid, case in FUSED.items() if case["evidence"]["fusion"]["native_decision"] == "review"})

    def test_signing_changes_no_decision_or_fusion(self):
        claims = [native(subject_id=pid, provider_record_id=f"r-{pid}") for pid in ("adrenaline", "one-past", "replay-lock", "weight-cheat")]
        plain = scored(read((claims, None)))
        signed = scored(read(([envelope(claim) for claim in claims], None)))
        for pid in plain:
            self.assertEqual(plain[pid]["decision"], signed[pid]["decision"], pid)
            self.assertEqual(plain[pid]["reasons"], signed[pid]["reasons"], pid)
            self.assertEqual(plain[pid]["evidence"].get("fusion"), signed[pid]["evidence"].get("fusion"), pid)
        self.assertEqual({signed[pid]["decision"] for pid in ("replay-lock", "weight-cheat")}, {"review"})
        self.assertEqual(signed["one-past"]["decision"], "watch")

    def test_the_observation_and_provenance_say_how_it_was_checked(self):
        loaded = read(([envelope(native(subject_id="adrenaline"))], None))
        case = scored(loaded)["adrenaline"]
        (obs,) = external_obs(case)
        self.assertEqual(obs["evidence"]["authenticity"], {"status": "verified", "algorithm": "ed25519", "provider": "example-integrity",
                                                           "key_id": "2026-01", "key_status": "active", "registry": registry().digest})
        external = case["evidence"]["provenance"]["external"]
        self.assertEqual(external["registry"], registry().summary())
        self.assertEqual(external["policy"], {"require_signed": False})
        self.assertEqual(external["sources"][0]["authentication"], {"verified": 1})

    def test_require_signed_reads_only_verified_records(self):
        rows = [envelope(native(subject_id="adrenaline")), native(subject_id="glitch"), envelope(native(subject_id="weak-human"), label="2027-01")]
        loaded = read((rows, None), require_signed=True)
        self.assertEqual(sorted(loaded.by_subject), ["adrenaline"])
        self.assertEqual(loaded.sources[0].excluded, 2)
        cases = scored(loaded)
        self.assertEqual((cases["adrenaline"]["decision"], cases["glitch"]["decision"], cases["weak-human"]["decision"]), ("watch", "clean", "clean"))
        self.assertEqual(cases["glitch"]["evidence"]["provenance"]["external"]["policy"], {"require_signed": True})
        from tests.test_external import NATIVE, native_part

        for pid in NATIVE:
            self.assertEqual(native_part(cases[pid]), native_part(NATIVE[pid]), pid)

    def test_without_a_registry_signatures_are_not_checked_and_need_no_package(self):
        from fpsdet import auth

        signed = envelope(native(subject_id="adrenaline"))  # made with the package, by the provider
        with mock.patch.object(auth, "_ed25519", side_effect=auth.AuthUnavailable("needs cryptography")):
            loaded = read(([signed], None), registry_rows=None)
        self.assertEqual(loaded.for_subject("adrenaline")[0].auth.status, "not_checked")


@unittest.skipUnless(HAVE_CRYPTO, "signature checking needs the optional cryptography package")
class ExternalAuthCommandTest(unittest.TestCase):
    def cli(self, *argv):
        import contextlib
        import io

        from fpsdet.cli import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else 1
                err.write(str(stop.code))
        return code, out.getvalue() + err.getvalue()

    def test_verify_and_score_with_a_registry(self):
        from fpsdet.persist import event_to_dict
        from tests.test_external import EVENTS

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            bad = envelope(native(subject_id="adrenaline"))
            bad["claim"]["kind"] = "x"
            bad_path = write_lines(folder, "bad.ndjson", [bad])
            reg = str(EXAMPLES / "registry.json")
            code, printed = self.cli("external", "verify", "--external-mapped", str(EXAMPLES / "example-integrity.adapter.json"),
                                     str(EXAMPLES / "example-integrity-signed.ndjson"), "--external-registry", reg)
            self.assertEqual(code, 0, printed)
            self.assertIn("signatures: verified 3", printed)
            code, printed = self.cli("external", "verify", "--external", str(bad_path), "--external-registry", reg)
            self.assertEqual(code, 1)
            self.assertIn("signature invalid", printed)
            events = folder / "events.ndjson"
            events.write_text("".join(json.dumps(event_to_dict(e)) + "\n" for e in EVENTS))
            profile = str(Path(__file__).resolve().parents[1] / "profiles" / "example-loadout.json")
            code, printed = self.cli("score", str(events), "--profile", profile, "--external-mapped", str(EXAMPLES / "example-integrity.adapter.json"),
                                     str(EXAMPLES / "example-integrity-signed.ndjson"), "--external-registry", reg, "--require-signed-external",
                                     "--out", str(folder / "out"))
            self.assertEqual(code, 0, printed)
            self.assertIn("signatures: verified 3", printed)
            code, printed = self.cli("score", str(events), "--profile", profile, "--external", str(bad_path), "--require-signed-external")
            self.assertNotEqual(code, 0)
            self.assertIn("needs --external-registry", printed)

    def test_16_no_output_carries_a_private_key(self):
        from fpsdet.ai_triage import triage_case
        from fpsdet.persist import event_to_dict
        from tests.test_challenge import leaks
        from tests.test_external import EVENTS

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            events = folder / "events.ndjson"
            events.write_text("".join(json.dumps(event_to_dict(e)) + "\n" for e in EVENTS))
            signed = write_lines(folder, "signed.ndjson", [envelope(native(subject_id="adrenaline")), envelope(native(subject_id="weak-human"), "2025-07")])
            code, printed = self.cli("score", str(events), "--profile", str(Path(__file__).resolve().parents[1] / "profiles" / "example-loadout.json"),
                                     "--external", str(signed), "--external-registry", str(EXAMPLES / "registry.json"), "--out", str(folder / "out"))
            self.assertEqual(code, 0, printed)
            surfaces = {"score output": printed}
            for path in sorted((folder / "out").rglob("*")):
                if path.is_file():
                    surfaces[path.name] = path.read_text(encoding="utf-8")
            case = next(row for row in json.loads((folder / "out" / "review-index.json").read_text())["cases"] if row["player_id"] == "adrenaline")
            self.assertEqual(case["decision"], "watch")
            sent: list[dict] = []
            triage_case(case, lambda body: sent.append(body) or "brief", known_ids=["adrenaline"])
            surfaces["AI brief input"] = json.dumps(sent)
            code, surfaces["verify output"] = self.cli("external", "verify", "--external", str(signed), "--external-registry", str(EXAMPLES / "registry.json"))
        for name, text in surfaces.items():
            with self.subTest(name):
                self.assertEqual(leaks(text, *SEEDS.values()), [])


def graph_nodes(case: dict, node_type: str) -> list[dict]:
    return [node for node in case["evidence"]["graph"]["nodes"] if node["type"] == node_type]


def graph_edges(case: dict, relation: str) -> list[tuple[str, str]]:
    return [(edge["source"], edge["target"]) for edge in case["evidence"]["graph"]["edges"] if edge["relation"] == relation]


@unittest.skipUnless(HAVE_CRYPTO, "signature checking needs the optional cryptography package")
class GraphV2Test(unittest.TestCase):
    """fpsdet.graph/2: who signed is a node of its own, apart from the group the record claims."""

    def cases(self):
        rows = [envelope(native(subject_id="adrenaline", provider_record_id="a1")),
                envelope(native(subject_id="adrenaline", provider_record_id="a2", kind="second")),
                native(subject_id="glitch"),
                envelope(native(subject_id="weak-human"), label="2027-01")]
        return scored(read((rows, None)))

    def test_a_verified_record_is_authenticated_by_its_key(self):
        case = self.cases()["adrenaline"]
        (key,) = graph_nodes(case, "provider_key")
        self.assertEqual(key["id"], "provider_key:example-integrity/2026-01")
        self.assertEqual(key["attributes"], {"provider": "example-integrity", "key_id": "2026-01", "algorithm": "ed25519",
                                             "key_status": "active", "registry": registry().digest})
        records = [node["id"] for node in graph_nodes(case, "external_record")]
        self.assertEqual(sorted(graph_edges(case, "authenticated_by")), sorted((record, key["id"]) for record in records))
        self.assertEqual(graph_edges(case, "belongs_to"), [(key["id"], "provider_group:example-integrity")])
        self.assertEqual({node["attributes"]["authenticity"] for node in graph_nodes(case, "external_record")}, {"verified"})
        summary = case["evidence"]["graph"]["summary"]
        self.assertEqual(summary["independence"]["authentication"], {"verified": 2})
        self.assertEqual(summary["independence"]["authenticated_provider_groups"], ["example-integrity"])
        self.assertIn({"kind": "provider_key", "value": key["id"], "observations": sorted(f"observation:{o['observation_id']}" for o in external_obs(case))},
                      summary["shared"])
        from fpsdet.graph import verify_graph

        self.assertEqual(verify_graph(case), [])

    def test_an_unauthenticated_record_has_no_key(self):
        cases = self.cases()
        for pid, status in (("glitch", "unsigned"), ("weak-human", "unknown_key")):
            with self.subTest(pid):
                self.assertEqual(graph_nodes(cases[pid], "provider_key"), [])
                (record,) = graph_nodes(cases[pid], "external_record")
                self.assertEqual(record["attributes"]["authenticity"], status)
                self.assertEqual(cases[pid]["evidence"]["graph"]["summary"]["independence"]["authenticated_provider_groups"], [])

    def test_a_forged_key_in_the_graph_is_caught(self):
        from fpsdet.graph import EvidenceGraph, describe, verify_graph

        case = copy.deepcopy(self.cases()["glitch"])
        stored = EvidenceGraph.from_dict({k: v for k, v in case["evidence"]["graph"].items() if k != "summary"})
        forged = EvidenceGraph("fpsdet.graph/2")
        for node in stored.nodes:
            forged.node(node.type, node.id.split(":", 1)[1], **node.attributes)
        for edge in stored.edges:
            forged.edge(edge.source, edge.relation, edge.target)
        (record,) = [node.id for node in stored.of_type("external_record")]
        key = forged.node("provider_key", "example-integrity/2026-01", provider="example-integrity", key_id="2026-01", algorithm="ed25519",
                          key_status="active", registry=registry().digest)
        forged.edge(record, "authenticated_by", key)
        case["evidence"]["graph"] = {**forged.to_dict(), "summary": describe(forged)}
        problems = " ".join(verify_graph(case))
        self.assertIn("does not match the case's evidence", problems)
        self.assertIn("shown as signed", problems)

    def test_older_graphs_and_packets_keep_their_meaning(self):
        from fpsdet.provenance import verify_packet

        expected = {"historical-packets-p23.json": ("fpsdet.packet/1", None), "historical-packets-p3.json": ("fpsdet.packet/1", None),
                    "historical-packets-p4.json": ("fpsdet.packet/1", None), "historical-packets-p5.json": ("fpsdet.packet/2", None),
                    "historical-packets-p6.json": ("fpsdet.packet/3", "fpsdet.graph/1"),
                    "historical-packets-p8.json": ("fpsdet.packet/4", "fpsdet.graph/2")}
        for name, (recipe, graph) in expected.items():
            for case in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"]:
                with self.subTest(name, player=case["player_id"]):
                    self.assertEqual(case["evidence"]["packet"]["recipe"], recipe)
                    self.assertEqual(case["evidence"].get("graph", {}).get("recipe"), graph)
                    self.assertEqual(verify_packet(case), [])

    def test_packet_4_binds_the_registry_and_the_policy(self):
        from fpsdet.provenance import packet_block, packet_material, verify_packet

        case = self.cases()["adrenaline"]
        self.assertEqual(case["evidence"]["packet"]["recipe"], "fpsdet.packet/5")
        external = packet_material(case, "fpsdet.packet/4")["provenance"]["external"]
        self.assertEqual(packet_material(case)["provenance"]["external"], external)
        self.assertEqual((external["registry"], external["policy"]), (registry().summary(), {"require_signed": False}))
        for name, edit in {"the registry": lambda e: e.update(registry={**e["registry"], "digest": "sha256:" + "0" * 64}),
                           "the policy": lambda e: e.update(policy={"require_signed": True})}.items():
            with self.subTest(name):
                edited = copy.deepcopy(case)
                edit(edited["evidence"]["provenance"]["external"])
                self.assertTrue(verify_packet(edited))
                # packet/3 would not have noticed: that is why the recipe moved.
                self.assertEqual(packet_block(edited, "fpsdet.packet/3")["digest"], packet_block(case, "fpsdet.packet/3")["digest"])

    def test_a_v4_digest_is_the_same_on_every_python(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_packet import fixed_case
        from fpsdet.graph import graph_block
        from fpsdet.provenance import packet_block

        case = fixed_case()
        case["match_ids"] = ["m1"]
        case["evidence"]["provenance"]["external"] = {"mode": "none"}
        case["evidence"]["graph"] = graph_block(case)
        self.assertEqual((case["evidence"]["graph"]["recipe"], case["evidence"]["graph"]["digest"]),
                         ("fpsdet.graph/2", "sha256:8e54e045e4129f2195f1229709d8b460d65865bbb1aa3ff42914d1258c1a1921"))
        self.assertEqual(packet_block(case, "fpsdet.packet/4"), {"recipe": "fpsdet.packet/4", "status": "complete",
                                              "digest": "sha256:5e7d3aaec90bb28d5b2f2d1a029b9256e142ee0b3570318b502b7062c0ebea59"})


@unittest.skipUnless(HAVE_CRYPTO, "signature checking needs the optional cryptography package")
class VerifyOnceTest(unittest.TestCase):
    def test_a_repeated_signature_is_checked_once_and_a_changed_one_again(self):
        from fpsdet import auth

        signed = envelope(native(subject_id="adrenaline"))
        tampered = copy.deepcopy(signed)
        tampered["claim"]["kind"] = "changed"
        calls = []
        real = auth.Registry._verify

        def counting(self, *args):
            calls.append(args)
            return real(self, *args)

        with mock.patch.object(auth.Registry, "_verify", counting):
            loaded = read(([signed] * 5 + [tampered], None))
        self.assertEqual(len(calls), 2)
        self.assertEqual(loaded.sources[0].authentication, {"invalid": 1, "verified": 5})
        self.assertEqual((loaded.sources[0].records, loaded.sources[0].duplicates), (1, 4))


if __name__ == "__main__":
    unittest.main()
