# Authenticated external evidence

## What a signature means, and what it does not

An external record says it comes from a provider. A signature lets fpsdet check that. The provider signs the exact claim with its private key, and fpsdet verifies it with the public key the operator registered for that provider.

`verified` means one thing: the registered, unrevoked key signed this exact claim. It does not mean the claim is true. It does not mean the provider is right, calibrated or independent of anyone else. A verified signature makes no record count for more than it did before: every external record is still a watch at most ([external-evidence.md](external-evidence.md)). What it changes is that fpsdet can now tell "a record that says it is from provider X" from "provider X's key signed this". Any later rule that wants to count sources needs that distinction first.

## Algorithm and dependency

Ed25519 (RFC 8032): a modern, standard signature scheme with small keys and deterministic signatures. Python's standard library has no Ed25519, and fpsdet does not implement cryptography itself, so verification uses the `cryptography` package (PyCA), version 42 or later.

- **Optional.** It is the `auth` extra: `pip install 'fpsdet[auth]'`. Everything else in fpsdet stays standard library only, and runs without it. Only checking signatures needs it.
- **Why this package.** It is the most widely deployed Python cryptography library, maintained by the Python Cryptographic Authority, with prebuilt wheels for every supported Python and platform. It is often already installed, and often available as a system package. The floor is a currently supported release line.
- **What it costs.** A compiled wheel of a few megabytes. Its security fixes become fpsdet's to pick up; keep it updated like any other dependency of the scoring host.
- **Without it.** A signed record read without a registry is reported as `not_checked`. Asking fpsdet to check signatures, by giving it a registry, without the package installed is an error that says how to install it. fpsdet never treats an unchecked signature as verified.
- **Not HMAC.** A shared-secret MAC would mean the operator holds the provider's signing secret, and could then forge the provider's records. With Ed25519 the operator holds only the public key.

## The registry: `fpsdet.provider-registry/1`

The operator's trust configuration: which providers it trusts, by which keys. Public keys only.

```json
{"format": "fpsdet.provider-registry/1",
 "providers": {
   "example-integrity": {"keys": [
     {"key_id": "2026-01", "algorithm": "ed25519", "public_key": "<base64, 32 bytes>", "status": "active"},
     {"key_id": "2025-07", "algorithm": "ed25519", "public_key": "<base64, 32 bytes>", "status": "retired"},
     {"key_id": "2024-11", "algorithm": "ed25519", "public_key": "<base64, 32 bytes>", "status": "revoked"}]}}}
```

It is read strictly: only these fields, a known algorithm and status, a provider id in the external format's charset, a key id of letters, digits, `.`, `_` and `-`, and a public key that decodes to a valid 32-byte Ed25519 key. A key listed twice is refused, and so is any field that looks like private material (`private_key`, `seed`, `secret`), loudly. The file is at most 256 KB.

Its identity is SHA-256 over `fpsdet.provider-registry/1`, a zero byte, and the canonical JSON of every key (provider, key id, algorithm, public key, status), sorted by provider and key id. Order in the file does not show. Adding, removing or changing a key, or changing its status, moves it. `examples/external/registry.json` is the fictional `example-integrity` provider's registry; its private seeds are committed only in `tests/fixtures/auth-test-keys.json`, marked as public test keys.

**A record never brings its own key.** The registry comes only from a file the operator names. A signed record that carries a key, a certificate, or any field beyond the signature's four is refused.

## What is signed: `fpsdet.external-signature/1`

```text
signed bytes = "fpsdet.external-signature/1" || 0x00 || canonical JSON of the claim
```

Canonical JSON is sorted keys, no spaces, UTF-8, with no NaN or Infinity: the same canonical form fpsdet uses for every identity. The prefix is used for nothing else, so a signature made for this cannot be valid for anything else, and a provider's signature over the bare JSON does not verify. A signed record is an envelope:

```json
{"format": "fpsdet.external-signed/1",
 "claim": {"format": "fpsdet.external/1", "provider": "example-integrity", "source_class": "client_integrity", "...": "..."},
 "signature": {"algorithm": "ed25519", "provider": "example-integrity", "key_id": "2026-01", "value": "<base64, 64 bytes>"}}
```

`tests/test_auth.py` pins a test vector: the signed bytes' SHA-256, and the exact signature the public test key makes over a fixed claim.

## Authentication states

| State | Meaning | What happens to the record |
| --- | --- | --- |
| `unsigned` | No signature | Read as an unauthenticated claim, as before |
| `not_checked` | Signed, and the run was given no registry | Read as an unauthenticated claim |
| `unknown_key` | Signed with a key the registry does not have | Read as an unauthenticated claim; never authenticated |
| `verified` | The registered, unrevoked key signed this exact claim | Read, and marked verified, with the key |
| `invalid` | The signature does not verify, or names another provider than the record is read as | Refused, never evidence |
| `revoked_key` | Signed with a key the operator revoked, valid or not | Refused, never evidence |
| `unsupported_algorithm` | Signed with an algorithm fpsdet does not verify | Refused, never evidence |

A malformed envelope or signature is refused like any malformed line.

## Reading signed records

```bash
fpsdet score events.ndjson --profile profiles/your-game.json \
    --external-mapped examples/external/example-integrity.adapter.json examples/external/example-integrity-signed.ndjson \
    --external-registry examples/external/registry.json [--require-signed-external]
fpsdet external verify --external-mapped ADAPTER RECORDS --external-registry REGISTRY
```

- **Any external file can hold signed envelopes,** line by line, beside unsigned records. A line is an envelope only if it is exactly `format`, `claim` and `signature`, with format `fpsdet.external-signed/1`. Nothing is guessed.
- **Verify, then read.** The signature is checked over the exact claim before anything maps or normalizes it. Only then is the claim read: directly if it is an `fpsdet.external/1` record, or through its adapter if it is the provider's own record.
- **The provider must match.** The signature must name the provider the record will be read as: the claim's own `provider`, or the adapter's. A provider's signature cannot be carried into another provider's identity, whatever an adapter says.
- **Refused states are errors.** `invalid`, `revoked_key` and `unsupported_algorithm` lines are skipped and named, like malformed lines, and counted on their source. `--external-strict` stops the run on them instead.
- **One claim, its strongest signature.** The same claim read twice is one record, as before. If one copy is verified, the record is verified, whichever file or order it came in. The claim's identity (`ext-…`) does not depend on any signature, so the same claim signed by a rotated key is still the same record.
- **`--require-signed-external`** reads only records whose signature verified. Unsigned, unchecked and unknown-key records are counted as excluded. Native scoring and every native decision are untouched. It needs `--external-registry`, and it is off by default, so unsigned records from community servers, research data and providers without keys keep working.
- **`fpsdet external verify`** reads the files and reports, for each, how many records were read, repeated and refused, and every line's signature state. It exits non-zero if any line was refused. It scores nothing.

### Where the adapter boundary is

The provider signs its own record, before any adapter touches it. fpsdet verifies that record, and then the adapter maps it. So an adapter cannot change a signed field and keep the signature: the signature was checked over the provider's record, not over the adapter's output. An adapter can still choose how to read the provider's fields: which path is the subject, which kinds are adverse, what the provider's score means. That interpretation is the operator's, not the provider's. It is bound by the adapter's digest in each source's provenance, as before, and a verified signature says nothing about it.

### In the case

Each external observation's `evidence.authenticity` says what was established:

```json
{"status": "verified", "algorithm": "ed25519", "provider": "example-integrity", "key_id": "2026-01",
 "key_status": "active", "registry": "sha256:…"}
```

An unsigned record says `{"status": "unsigned"}`. Authentication is part of the observation's identity, so the same claim, signed or not, is one record but two different observations. `provenance.external` also gains:
- `registry`: the recipe, digest and key count of the registry, or null;
- `policy`: `{"require_signed": …}`;
- for each source, `excluded` and `authentication`, a count of every readable line by signature state.

## In the graph and the packet

`fpsdet.graph/2` gives a verified record an `authenticated_by` edge to a `provider_key` node (provider, key id, algorithm, key status, registry digest), and that key a `belongs_to` edge to the provider group the record claims. The key and the group stay separate nodes: who signed is not the same fact as who the record says stands behind it. Unsigned, unchecked and unknown-key records have no key node, and their `authenticity` attribute says why ([evidence-graph.md](evidence-graph.md)).

`fpsdet.packet/4` binds the registry digest and the signature policy, beside everything `packet/3` binds ([provenance.md](provenance.md)). With each observation's own `authenticity`, which is bound by its id, a packet says which records were verified, by which keys, under which registry, and whether unsigned records were allowed.

## Rotation and revocation

- **`active`**: the provider signs with it now. Its signatures verify.
- **`retired`**: the provider no longer signs with it. What it signed before still verifies, and says `key_status: retired`. A provider rotates by adding a new key as `active` and moving the old one to `retired`; its identity does not change.
- **`revoked`**: the operator no longer trusts anything it signed, for example after a compromise. A record signed with it is refused, even if the signature is mathematically valid. Without a signed time there is no way to tell what it signed before the compromise from what was signed after.
- **No validity windows.** A record's own `observed_at` is optional, and nothing says it was signed at that time, so fpsdet does not pretend to check key expiry against it. Revocation is the operator's lever.
- **No PKI.** No certificates, chains or online checks. The registry is the trust anchor, and changing trust means changing the registry, which moves its digest.

## What it is for, and what it is not

Authentication is a prerequisite, not a policy. Any future rule that lets external evidence count for more has to know which records really came from which provider. "Two independent providers agree" means nothing if anyone can type two provider names. The graph already says which records share a provider group or a telemetry domain; now it also says which records a registered key signed, and which key. A later, calibrated rule can require both: verified sources, and groups and domains that are actually different. This change does not add that rule. A verified record makes exactly the watch an unsigned one makes, and never a review.

What it does not do:

- **Prove a claim.** A provider can sign a wrong record. `verified` is about who, never about whether.
- **Vouch for an adapter.** The signature covers the provider's record. How an adapter reads it is the operator's configuration, bound by the adapter's digest.
- **Stop unsigned records by default.** Unsigned records are still read, so a forged unsigned record can still make a watch. `--require-signed-external` is the switch.
- **Detect a stolen key.** If a provider's private key leaks, records signed with it verify until the operator marks it `revoked`.
- **Exist for any real anti-cheat.** No VAC, EAC, BattlEye or Vanguard signing key is in any registry here. A provider that signs its records can be added to an operator's registry.

## What changed when it arrived

Every existing data set was scored again with no external input and compared with the evidence graph (`ac5dfab`):

| | Cases | Decisions | Reasons | Observations | Observation ids | Seals | Graphs | Packets |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Planted | 32 | 0 | 0 | 0 | 0 | 0 | 32 | 32 |
| Synthetic weekly | 400 | 0 | 0 | 0 | 0 | 0 | 400 | 400 |
| Synthetic nightly | 2,669 | 0 | 0 | 0 | 0 | 0 | 2,669 | 2,669 |
| CS2 | 1,529 | 0 | 0 | 0 | 0 | 0 | 1,529 | 1,529 |
| TF2 | 2,764 | 0 | 0 | 0 | 0 | 0 | 2,764 | 2,764 |

- **Graphs:** every graph moved from `fpsdet.graph/1` to `fpsdet.graph/2` with identical nodes and edges. Only the recipe and two empty summary fields changed.
- **Packets:** every packet moved from `fpsdet.packet/3` to `fpsdet.packet/4`.
- **Verification:** every case verifies.
- **The external fixtures,** scored with the same records before and after:
  - every decision, reason, seal, fusion block, native observation id and record id is the same;
  - all 19 external observations moved id, because their `authenticity` now says `{"status": "unsigned"}` instead of `"unverified"`.
- **Without the package:** on a Python without `cryptography`, the demo and every test but the 39 that need it pass.

**Cost**, measured on one machine:

| | Before | After |
| --- | --- | --- |
| Scoring with no external input: synthetic week, CS2, TF2 | 1.25 s, 12.3 s, 13.7 s | 1.25 s, 11.9 s, 13.6 s |
| Writing the week's 400 cases | 0.027 s, 2.01 MB | 0.029 s, 2.04 MB |

| Records read | Unsigned | Signed, checked | Signed, no registry | One signature repeated |
| --- | --- | --- | --- | --- |
| 1,000 | 0.02 s | 0.10 s | 0.03 s | 0.03 s |
| 10,000 | 0.21 s | 0.98 s | 0.32 s | 0.28 s |
| 100,000 | 2.2 s | 10.3 s | 3.6 s | 2.8 s |

Checking a signature costs about 80 µs, almost all of it Ed25519. Each one is checked once per run: the same signature over the same bytes is not checked again. Keys are looked up by provider and key id.

## Before authentication: what fpsdet trusted

This is the external trust model at `58f1f50`, before signatures. `tests/test_auth.py` (`CurrentTrustModelTest`) pins it.

| | How it worked |
| --- | --- |
| Record identity | `ext-` and 24 hex digits of SHA-256 over the normalized claim (`fpsdet.external/1`). The same claim from any file or adapter is one record |
| Adapter mapping | Key-lookup paths, constants and an allowlist. The adapter, not the record, sets the provider, its group, the class and the domain |
| Provider and provider group | Whatever the record, or its adapter, said. A record naming `example-integrity` was treated as `example-integrity`'s, and one naming another provider could still claim `example-integrity`'s group |
| Telemetry domain | Whatever the record or adapter said |
| Authenticity | Always `unverified`, in every external observation's evidence |
| Graph | An `external_record` node (provider, class, direction), `provided_by` a `provider_group` node. No notion of a key |
| Packet | `fpsdet.packet/3` binds every external observation by id, `provenance.external` (mode, recipe, digest, records, sources), the fusion state and the graph |
| Malformed input | Skipped line by line with an error naming the file, line and reason, or the run stops with `--external-strict`. A `signature` field was an unknown field, and the line was refused |
| Fusion | An adverse record, of any class but account status, scoped to a scored match, made a clean case a watch. Never a review |

**Where a forged record could act.** Anyone who could write one line into an external-records file could name any provider, any group and any domain, about any player, in any scored match, and make that player's clean case a watch. The record would say `unverified`, which was true. But nothing distinguished it from a record that really came from that provider. Any later rule that counted "two independent providers" would have counted a forger who typed two names.
