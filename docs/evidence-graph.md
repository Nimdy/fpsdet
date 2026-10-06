# The evidence graph

## Why it exists

A case's evidence is a list of observations, and until now the relationships between them lived inside their evidence: a partner's name in one, a challenge's plan digest in another, an external record's provider group in a third. A reader had to know every detector's shape to see that two observations rest on the same thing.

The graph makes those relationships explicit, as typed nodes and typed edges. It answers:

- what supports this case, and what is only context;
- what each observation depends on, and what it was made from;
- which observations share a provider group, a telemetry domain, a challenge, a record, or a dependency;
- which player a relationship observation is measured against.

It records facts. It decides nothing and weighs nothing. It does not claim that two sources are independent: different provider names, provider groups or telemetry domains are not assumed to be independent. The graph says what each piece of evidence shares with the others. Deciding what that means is left to a later, calibrated policy.

## The model

`fpsdet.graph`, standard library only. A graph is typed nodes and a set of typed edges.

- **Closed.** The node types and relations below are fixed in code. Nothing in a case, an event or an external record can add a type or a relation, and an edge may only join the node types its relation allows.
- **Node ids are typed.** An id is its type, a colon, and a key: `observation:obs-…`, `player:p-7`, `provider_group:example-integrity`. Only the key comes from data.
- **Edges are a set.** The same source, relation and target is one edge, however often it is added. The same edge, or the same node, described two different ways is an error, never merged.
- **No floats, small attributes.** Attributes are strings, integers, booleans, nulls, and lists or objects of them, at most three levels deep, so identity is the same on every Python.
- **Derivation never goes round.** `depends_on` and `derived_from` must have no cycle: nothing can rest on itself. Other relations are not checked for cycles, because their meaning does not forbid one.

### Node types

| Type | Stands for | Key |
| --- | --- | --- |
| `case` | One player's evidence and decision in one run | the player |
| `player` | A player pseudonym: the case's subject, or a partner a relationship names | the pseudonym |
| `match` | A match | the match id |
| `observation` | An observation on this case, or one on another case that this case depends on | the observation id |
| `challenge` | A challenge's public identity: id, type, version, plan digest, commitment, window | the challenge id |
| `external_record` | One external record | its `ext-` id |
| `provider_group` | Who stands behind one or more external providers | the group |
| `telemetry_domain` | What an external signal was measured from | the domain |
| `cohort` | The cohort a run compared against | its digest |
| `history` | The account history a run read for this player | its digest |

### Relations

Each reads source → target.

| Relation | From | To | Meaning |
| --- | --- | --- | --- |
| `about` | case, observation, challenge, external record | player | The player it is about |
| `played_in` | player | match | A match the case's player played in the scored window |
| `occurred_in` | observation, challenge, external record | match | A match it was measured in or scoped to |
| `supports` | observation | case | It has a role in the case's decision. An external record that is context only does not |
| `depends_on` | observation | observation | It rests on that observation: exactly `Observation.depends_on` |
| `derived_from` | observation | challenge, external record | It was made from that challenge or record |
| `names_partner` | observation | player | The other player a relationship observation is measured against |
| `provided_by` | external record | provider group | The group it came from |
| `uses_telemetry_domain` | external record | telemetry domain | What it says it was measured from |
| `compared_against` | observation | cohort | The cohort its numbers were compared with |
| `uses_history` | observation | history | The account history it was compared with |

There is no `related_to`. Every edge says what the relationship is.

### Identity: `fpsdet.graph/1`

SHA-256 over `fpsdet.graph/1`, a zero byte, and the canonical JSON (sorted keys, no spaces, UTF-8) of:

```json
{"recipe": "fpsdet.graph/1",
 "nodes": [{"id": ..., "type": ..., "attributes": {...}}, ...],
 "edges": [{"source": ..., "relation": ..., "target": ..., "attributes": {...}}, ...]}
```

with nodes sorted by id and edges by source, relation and target. Order of insertion never shows, and a duplicate edge is one edge. Any added, removed or changed node, edge or attribute moves it. A serialized graph is read back strictly: a known recipe, every node a known type with an id that starts with it, every edge a known relation between existing nodes of allowed types, nothing listed twice, no cycle in a derivation, and a digest that matches.

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
