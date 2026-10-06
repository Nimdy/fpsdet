# Authenticated external evidence

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
