"""Authenticated external evidence: which provider's key signed a record, and nothing more.

CurrentTrustModelTest was written before authentication (P7, phase A0) to pin what fpsdet trusted about an
external record: nothing. Any line naming any provider was that provider's claim.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
