# The evidence graph

## Before the graph: where relationships live

This inventory was written at `2913fa3`, before the graph existed. `tests/test_graph.py` (`CurrentRelationshipsTest`) pins it.

| Relationship | Where it is today | Explicit or implicit |
| --- | --- | --- |
| Case → its player | `case.player_id`; every observation's `subject_id` equals it | Explicit, as a field |
| Case → its matches | `case.match_ids`; an observation's `match_ids` is a subset of them | Explicit, as fields |
| Observation → observation | `Observation.depends_on`, in its identity. Only the voice-speed teammate check uses it, and it points to the partner's hidden-mover observations, which are on the partner's case, not this one | Explicit, but across cases, by id only |
| Voice watch → partner | `evidence.partner` and `evidence.party_id` | Implicit: a value inside the evidence |
| Shared leftover → partner | `evidence.partner` and `evidence.partner_in_review`; no `depends_on` and no match | Implicit |
| Challenge observation → challenge plan | `evidence.challenge`: id, origin, type, version, commitment, plan digest, window; `key` is the challenge id. A legacy private replay says `legacy_private_replay` and has no plan | Implicit |
| Challenge results not followed | `evidence.challenges`, not bound by any packet | Implicit, unbound |
| External observation → external record | `evidence.external_id`, and `key` | Implicit |
| External record → provider, provider group, telemetry domain | Fields inside the external observation's evidence; the run's sources and the player's record digest in `provenance.external` | Implicit |
| Account-history observation → history | The windows' totals inside `evidence.history`; the history digest in `provenance.history` | Implicit: nothing links the observation to the digest |
| Human-baseline observation → cohort | Cohort lines inside its evidence; the cohort digest in `provenance.cohort` | Implicit |
| Packet → observations and provenance | `fpsdet.packet/2` binds the sorted observation ids, the provenance parts and the fusion state | Explicit, as a digest |
| Reports | `case.reports`: a queue-order count, never in evidence | Deliberately outside the evidence |

Three things follow:

- **The one explicit dependency crosses cases.** A voice watch rests on observations that live on another player's case. A graph built from one case has to represent them as references, and checking that they exist needs the other case.
- **Every other relationship is a value inside an evidence block.** A reader has to know each detector's evidence shape to find a partner, a plan, a provider group or a domain. Nothing checks that those values agree with provenance.
- **Nothing says which pieces of evidence share a source.** Two external records from one provider group, two observations from one challenge plan, or a voice watch and the hidden mover it rests on, look like separate entries in a flat list.
