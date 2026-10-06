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
        self.assertEqual({obs["evidence"]["authenticity"] for obs in rows}, {"unverified"})

    def test_a_line_naming_any_provider_is_that_providers_claim(self):
        # Anyone who can write to the external file can make a watch for a player in a scored match.
        cases = run([native(subject_id="adrenaline", provider="example-integrity", provider_record_id="forged-1")])
        self.assertEqual(cases["adrenaline"]["decision"], "watch")
        (obs,) = external_obs(cases["adrenaline"])
        self.assertEqual((obs["evidence"]["provider"], obs["evidence"]["authenticity"]), ("example-integrity", "unverified"))

    def test_a_record_chooses_its_own_provider_group(self):
        cases = run([native(subject_id="adrenaline", provider="someone-else", provider_group="example-integrity")])
        (obs,) = external_obs(cases["adrenaline"])
        self.assertEqual(obs["evidence"]["provider_group"], "example-integrity")

    def test_a_signature_field_is_refused_not_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            loaded = read_external([(write_lines(Path(folder), "r.ndjson", [native(signature={"value": "x"})]), None)])
        self.assertEqual((len(loaded.records), len(loaded.errors)), (0, 1))

    def test_the_graph_has_no_notion_of_a_key(self):
        from fpsdet.graph import NODE_TYPES, RELATIONS

        self.assertNotIn("provider_key", NODE_TYPES)
        graph = FUSED["blasted"]["evidence"]["graph"]
        records = [node for node in graph["nodes"] if node["type"] == "external_record"]
        self.assertEqual({tuple(sorted(node["attributes"])) for node in records}, {("direction", "provider", "source_class")})
        self.assertEqual(graph["recipe"], "fpsdet.graph/1")
        self.assertEqual(sorted(RELATIONS), sorted([
            "about", "occurred_in", "supports", "depends_on", "derived_from", "names_partner", "provided_by",
            "uses_telemetry_domain", "compared_against", "uses_history",
        ]))

    def test_packet_3_binds_these_external_fields(self):
        self.assertEqual(PACKET_V2_PROVENANCE["external"], ("mode", "recipe", "digest", "records", "sources"))
        case = FUSED["adrenaline"]
        self.assertEqual(case["evidence"]["packet"]["recipe"], "fpsdet.packet/3")
        self.assertEqual(sorted(case["evidence"]["provenance"]["external"]), ["digest", "mode", "recipe", "records", "sources"])
        self.assertEqual(sorted(case["evidence"]["provenance"]["external"]["sources"][0]), [
            "adapter", "adapter_name", "artifact", "duplicates", "errors", "records",
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


if __name__ == "__main__":
    unittest.main()
