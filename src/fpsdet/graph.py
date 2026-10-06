"""The evidence graph: what each piece of evidence is, where it came from, and what it depends on.

A case's evidence is a list of observations, and the relationships between them live inside their
evidence: a partner's name, a challenge's plan, an external record's provider group. The graph makes
those relationships explicit, as typed nodes and typed edges, so a reader can ask which observations
rest on the same source, and a later policy can tell independent evidence from the same signal seen
twice.

The graph records facts. It does not decide anything, weigh anything, or claim that two sources are
independent: different provider names, groups or telemetry domains are not assumed independent.

The model is small and closed. Node types and relations are fixed here; nothing in a case or an
external record can add one. Edges are a set: the same source, relation and target is one edge.
Identity (``fpsdet.graph/1``) is the SHA-256 of the canonical JSON of the sorted nodes and edges, so
insertion order never shows.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .evidence import canonical_json

GRAPH_V1 = "fpsdet.graph/1"
# graph/1 plus provider keys: which registered key signed an external record (fpsdet.auth).
GRAPH_V2 = "fpsdet.graph/2"
GRAPH_RECIPE = GRAPH_V2  # what new cases are written with

# Every node type, and what a node of it stands for.
NODE_TYPES: dict[str, str] = {
    "case": "the case: one player's evidence and decision in one run",
    "player": "a player pseudonym: the case's subject, or a partner a relationship names",
    "match": "a match some evidence was measured in or scoped to",
    "observation": "an observation: one on this case, or one on another case that this case depends on",
    "challenge": "a challenge's public identity: id, type, version, plan digest, commitment, window",
    "external_record": "one external record, by its fpsdet.external/1 id",
    "provider_group": "who stands behind one or more external providers",
    "telemetry_domain": "what a signal was measured from",
    "cohort": "the cohort a run compared against, by its digest",
    "history": "the account history a run read for one player, by its digest",
}

# Every relation: which node types it goes from and to, and what it means, read source → target.
RELATIONS: dict[str, tuple[frozenset[str], frozenset[str], str]] = {
    "about": (frozenset({"case", "observation", "challenge", "external_record"}), frozenset({"player"}), "the player it is about"),
    "occurred_in": (frozenset({"observation", "challenge", "external_record"}), frozenset({"match"}), "a match it was measured in or scoped to"),
    "supports": (frozenset({"observation"}), frozenset({"case"}), "it has a role in the case's decision"),
    "depends_on": (frozenset({"observation"}), frozenset({"observation"}), "it rests on that observation (Observation.depends_on)"),
    "derived_from": (frozenset({"observation"}), frozenset({"challenge", "external_record"}), "it was made from that challenge or record"),
    "names_partner": (frozenset({"observation"}), frozenset({"player"}), "the other player a relationship observation is measured against"),
    "provided_by": (frozenset({"external_record"}), frozenset({"provider_group"}), "the provider group the record came from"),
    "uses_telemetry_domain": (frozenset({"external_record"}), frozenset({"telemetry_domain"}), "what the record says it was measured from"),
    "compared_against": (frozenset({"observation"}), frozenset({"cohort"}), "the cohort its numbers were compared with"),
    "uses_history": (frozenset({"observation"}), frozenset({"history"}), "the account history it was compared with"),
}
# graph/1 is exactly the types and relations above; graph/2 adds a signing key and its two relations.
NODE_TYPES_V1 = dict(NODE_TYPES)
RELATIONS_V1 = dict(RELATIONS)
NODE_TYPES["provider_key"] = "a provider's registered signing key, by provider and key id; only for a signature that verified"
RELATIONS["authenticated_by"] = (frozenset({"external_record"}), frozenset({"provider_key"}), "the registered key that signed the record")
RELATIONS["belongs_to"] = (frozenset({"provider_key"}), frozenset({"provider_group"}), "the provider group a verified record signed with this key claims")
SCHEMAS = {GRAPH_V1: (NODE_TYPES_V1, RELATIONS_V1), GRAPH_V2: (NODE_TYPES, RELATIONS)}
# Relations whose meaning forbids a cycle: nothing can rest on, or be made from, itself.
ACYCLIC = frozenset({"depends_on", "derived_from"})

# Graph attributes are small plain data. No floats: identity must be the same on every Python.
_SCALARS = (str, int, bool, type(None))
MAX_ATTRIBUTE_DEPTH = 3


class GraphError(ValueError):
    """A graph that is not well formed: an unknown type or relation, a missing endpoint, a conflict, a cycle."""


def _check_value(value, where: str, depth: int = 0) -> None:
    if isinstance(value, float):
        raise GraphError(f"{where}: graph attributes hold no floats")
    if isinstance(value, _SCALARS):
        return
    if depth >= MAX_ATTRIBUTE_DEPTH:
        raise GraphError(f"{where}: nested too deep")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise GraphError(f"{where}: keys must be strings")
            _check_value(item, f"{where}.{key}", depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_value(item, f"{where}[{index}]", depth + 1)
        return
    raise GraphError(f"{where}: {type(value).__name__} is not graph data")


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def node_id(node_type: str, key: str, recipe: str = GRAPH_RECIPE) -> str:
    """A node's id: its type, a colon, and its key. The type is fpsdet's; only the key comes from data."""
    if node_type not in SCHEMAS[recipe][0]:
        raise GraphError(f"unknown node type {node_type!r}")
    if not isinstance(key, str) or not key:
        raise GraphError(f"a {node_type} node needs a non-empty string key")
    return f"{node_type}:{key}"


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    attributes: Mapping

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "attributes": _plain(self.attributes)}


@dataclass(frozen=True)
class Edge:
    source: str
    relation: str
    target: str
    attributes: Mapping

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.relation, self.target)

    def to_dict(self) -> dict:
        return {"source": self.source, "relation": self.relation, "target": self.target, "attributes": _plain(self.attributes)}


class EvidenceGraph:
    """Typed nodes and a set of typed edges. Order of insertion never matters."""

    def __init__(self, recipe: str = GRAPH_RECIPE) -> None:
        if recipe not in SCHEMAS:
            raise GraphError(f"no graph recipe {recipe!r}")
        self.recipe = recipe
        self._types, self._relations = SCHEMAS[recipe]
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}
        # Each edge indexed from both ends, so a lookup costs the node's degree, not the graph's size.
        self._out: dict[tuple[str, str], set[str]] = {}
        self._in: dict[tuple[str, str], set[str]] = {}

    def node(self, node_type: str, key: str, **attributes) -> str:
        """Add a node, or find it. The same id with different attributes is a conflict, never a merge."""
        identity = node_id(node_type, key, self.recipe)
        _check_value(attributes, identity)
        attributes = _plain(attributes)
        known = self._nodes.get(identity)
        if known is not None:
            if known.attributes != attributes:
                raise GraphError(f"{identity} is described two different ways")
            return identity
        self._nodes[identity] = Node(identity, node_type, attributes)
        return identity

    def edge(self, source: str, relation: str, target: str, **attributes) -> None:
        """Add an edge. The same source, relation and target is one edge; differing attributes are a conflict."""
        if relation not in self._relations:
            raise GraphError(f"unknown relation {relation!r} in {self.recipe}")
        for end in (source, target):
            if end not in self._nodes:
                raise GraphError(f"{relation} names {end}, which is not a node")
        sources, targets, _meaning = self._relations[relation]
        if self._nodes[source].type not in sources or self._nodes[target].type not in targets:
            raise GraphError(f"{relation} cannot go from a {self._nodes[source].type} to a {self._nodes[target].type}")
        _check_value(attributes, f"{source} {relation} {target}")
        made = Edge(source, relation, target, _plain(attributes))
        known = self._edges.get(made.key)
        if known is not None and known.attributes != made.attributes:
            raise GraphError(f"{source} {relation} {target} is described two different ways")
        self._edges[made.key] = made
        self._out.setdefault((source, relation), set()).add(target)
        self._in.setdefault((target, relation), set()).add(source)

    @property
    def nodes(self) -> list[Node]:
        return [self._nodes[key] for key in sorted(self._nodes)]

    @property
    def edges(self) -> list[Edge]:
        return [self._edges[key] for key in sorted(self._edges)]

    def get(self, identity: str) -> Node | None:
        return self._nodes.get(identity)

    def out(self, source: str, relation: str) -> list[str]:
        return sorted(self._out.get((source, relation), ()))

    def into(self, target: str, relation: str) -> list[str]:
        return sorted(self._in.get((target, relation), ()))

    def of_type(self, node_type: str) -> list[Node]:
        return [node for node in self.nodes if node.type == node_type]

    def check_acyclic(self) -> None:
        """No cycle through a relation whose meaning forbids one. Iterative, linear in nodes and edges."""
        for relation in sorted(ACYCLIC):
            following: dict[str, list[str]] = {}
            for source, kind, target in self._edges:
                if kind == relation:
                    following.setdefault(source, []).append(target)
            state: dict[str, int] = {}  # 1 on the current path, 2 finished
            for start in sorted(following):
                if state.get(start):
                    continue
                stack = [(start, iter(sorted(following.get(start, ()))))]
                state[start] = 1
                while stack:
                    current, children = stack[-1]
                    child = next(children, None)
                    if child is None:
                        state[current] = 2
                        stack.pop()
                    elif state.get(child) == 1:
                        raise GraphError(f"{relation} goes round in a cycle through {child}")
                    elif not state.get(child):
                        state[child] = 1
                        stack.append((child, iter(sorted(following.get(child, ())))))

    def material(self) -> dict:
        return {
            "recipe": self.recipe,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def digest(self) -> str:
        """``fpsdet.graph/1``: SHA-256 over the recipe, a zero byte, and the canonical JSON of the nodes
        sorted by id and the edges sorted by source, relation and target."""
        body = canonical_json(self.material())
        return "sha256:" + hashlib.sha256(self.recipe.encode("ascii") + b"\0" + body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {**self.material(), "digest": self.digest()}

    @classmethod
    def from_dict(cls, obj) -> EvidenceGraph:
        """A serialized graph, checked: its recipe, every node's type and id, every edge's relation and
        endpoints, no node or edge listed twice, no forbidden cycle, and its digest."""
        if not isinstance(obj, Mapping) or obj.get("recipe") not in SCHEMAS:
            raise GraphError(f"not a graph of a known recipe ({', '.join(SCHEMAS)})")
        nodes, edges = obj.get("nodes"), obj.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise GraphError("nodes and edges must be lists")
        graph = cls(obj["recipe"])
        for row in nodes:
            if not isinstance(row, Mapping) or set(row) != {"id", "type", "attributes"} or not isinstance(row["attributes"], Mapping):
                raise GraphError("a node is an id, a type and attributes")
            node_type = row["type"]
            prefix = f"{node_type}:" if isinstance(node_type, str) else ""
            if not isinstance(row["id"], str) or not row["id"].startswith(prefix) or not prefix:
                raise GraphError("a node's id must be its type and a key")
            if row["id"] in graph._nodes:
                raise GraphError(f"{row['id']} is listed twice")
            graph.node(node_type, row["id"][len(prefix):], **row["attributes"])
        for row in edges:
            if not isinstance(row, Mapping) or set(row) != {"source", "relation", "target", "attributes"} or not isinstance(row["attributes"], Mapping):
                raise GraphError("an edge is a source, a relation, a target and attributes")
            if (row["source"], row["relation"], row["target"]) in graph._edges:
                raise GraphError(f"{row['source']} {row['relation']} {row['target']} is listed twice")
            graph.edge(row["source"], row["relation"], row["target"], **row["attributes"])
        graph.check_acyclic()
        if "digest" in obj and obj["digest"] != graph.digest():
            raise GraphError("the graph digest does not match its nodes and edges")
        return graph


def schema(recipe: str = GRAPH_RECIPE) -> dict:
    """A recipe's node types and relations, as data, for documentation and for a later reader."""
    types, relations = SCHEMAS[recipe]
    return {
        "recipe": recipe,
        "node_types": dict(types),
        "relations": {
            name: {"from": sorted(sources), "to": sorted(targets), "meaning": meaning, "acyclic": name in ACYCLIC}
            for name, (sources, targets, meaning) in relations.items()
        },
    }



# Building a case's graph. Everything comes from the serialized case: its observations, their evidence,
# and its provenance. Nothing is inferred that the case does not already say, and provider text never
# becomes a node type, a relation or a key beyond the checked ids it already is.

# Roles that take part in a decision. An external record that is context only does not support the case.
SUPPORTING_ROLES = frozenset({"review", "past_human", "account_change", "supporting", "watch", "external_watch"})
# What fpsdet's own evidence was measured from, in the same words as external telemetry domains.
SERVER_BEHAVIOR = "server_behavior"
SERVER_CHALLENGE = "server_challenge"


def _authenticity(claim: Mapping) -> dict:
    """An external observation's signature state as data; an observation from before signatures said a word."""
    value = claim.get("authenticity")
    return dict(value) if isinstance(value, Mapping) else {"status": str(value)}


def build_graph(case: Mapping, recipe: str = GRAPH_RECIPE) -> EvidenceGraph:
    """The evidence graph of one serialized case (``persist.case_to_dict``), without its graph block, built
    by ``recipe``. A graph is rebuilt with the recipe it was written with, so an old one keeps its meaning."""
    graph = EvidenceGraph(recipe)
    evidence = case["evidence"]
    provenance = evidence.get("provenance") or {}
    rows = evidence["observations"]
    subject = graph.node("player", case["player_id"])
    case_node = graph.node("case", case["player_id"], decision=case["decision"])
    # The case's matches stay in case.match_ids. A match is a node only where evidence occurred in it, so a
    # graph grows with the evidence, not with how many matches the player played.
    graph.edge(case_node, "about", subject)
    cohort = provenance.get("cohort") if isinstance(provenance.get("cohort"), Mapping) else None
    history = provenance.get("history") if isinstance(provenance.get("history"), Mapping) else None
    # Every observation of this case first, so a dependency between two of them finds its target.
    local = {obs["observation_id"]: graph.node(
        "observation", obs["observation_id"], in_case=True, source=obs["source"], family=obs["family"], kind=obs["kind"], role=obs["role"],
    ) for obs in rows}
    for obs in rows:
        node = local[obs["observation_id"]]
        graph.edge(node, "about", graph.node("player", obs["subject_id"]))
        for match in obs["match_ids"]:
            graph.edge(node, "occurred_in", graph.node("match", match))
        if obs["role"] in SUPPORTING_ROLES:
            graph.edge(node, "supports", case_node)
        partner = obs["evidence"].get("partner") if obs["family"] == "relationship" else None
        if partner is not None:
            graph.edge(node, "names_partner", graph.node("player", partner))
        for dependency in obs["depends_on"]:
            target = local.get(dependency)
            if target is None:
                # On another case. All this case knows is its id, and whose evidence it is: the partner's.
                target = graph.node("observation", dependency, in_case=False)
                if partner is not None:
                    graph.edge(target, "about", graph.node("player", partner))
            graph.edge(node, "depends_on", target)
        if obs["family"] == "challenge":
            claim = obs["evidence"]["challenge"]
            challenge = graph.node(
                "challenge", claim["challenge_id"], origin=claim["origin"], type=claim["type"], version=claim["version"],
                commitment=claim["commitment"], plan=claim["plan"], window=claim["window"],
            )
            graph.edge(node, "derived_from", challenge)
            graph.edge(challenge, "about", subject)
            if claim["origin"] == "planned":
                for match in obs["match_ids"]:
                    graph.edge(challenge, "occurred_in", graph.node("match", match))
        elif obs["family"] == "external":
            claim = obs["evidence"]
            attributes = dict(provider=claim["provider"], source_class=claim["source_class"], direction=claim["direction"])
            auth = _authenticity(claim)
            if recipe == GRAPH_V2:
                attributes["authenticity"] = auth["status"]
            record = graph.node("external_record", claim["external_id"], **attributes)
            graph.edge(node, "derived_from", record)
            graph.edge(record, "about", subject)
            if claim["scope"]["match_id"] is not None:
                graph.edge(record, "occurred_in", graph.node("match", claim["scope"]["match_id"]))
            group = graph.node("provider_group", claim["provider_group"])
            graph.edge(record, "provided_by", group)
            graph.edge(record, "uses_telemetry_domain", graph.node("telemetry_domain", claim["telemetry_domain"]))
            if recipe == GRAPH_V2 and auth["status"] == "verified":
                # The key is a separate node from the group: who signed is not the same fact as who the record says it is.
                key = graph.node(
                    "provider_key", f"{auth['provider']}/{auth['key_id']}", provider=auth["provider"], key_id=auth["key_id"],
                    algorithm=auth["algorithm"], key_status=auth["key_status"], registry=auth["registry"],
                )
                graph.edge(record, "authenticated_by", key)
                graph.edge(key, "belongs_to", group)
        elif obs["family"] == "human_baseline" and cohort and cohort.get("digest"):
            graph.edge(node, "compared_against", graph.node(
                "cohort", cohort["digest"], mode=cohort.get("mode"), recipe=cohort.get("recipe"),
                integrity=(cohort.get("integrity") or {}).get("status"),
            ))
        elif obs["family"] == "account_history" and history and history.get("digest"):
            graph.edge(node, "uses_history", graph.node(
                "history", history["digest"], mode=history.get("mode"), recipe=history.get("recipe"), windows=history.get("windows"),
            ))
    return graph


def describe(graph: EvidenceGraph) -> dict:
    """What the graph says about independence, as facts. No score, and nothing here changes a decision.

    ``observations`` describes each of this case's observations: native or external, the family, whether
    it is server-authoritative, challenge-derived or relationship-derived, its provider group and
    telemetry domain (plain values), and the record, challenge, partner and dependencies it points to
    (node ids). fpsdet's own evidence is ``server_behavior``, or ``server_challenge`` for a challenge. ``independence`` lists what the supporting evidence comes from. ``shared`` lists
    every provider group, telemetry domain, record, challenge, dependency and partner that two or more
    observations have in common: evidence that is not separate, whatever its names say.
    """
    described: dict[str, dict] = {}
    for node in graph.of_type("observation"):
        if not node.attributes.get("in_case"):
            continue
        attributes = node.attributes
        external = attributes["source"] == "external"
        record = (graph.out(node.id, "derived_from") or [None])[0] if external else None
        challenge = (graph.out(node.id, "derived_from") or [None])[0] if attributes["family"] == "challenge" else None
        group_node = (graph.out(record, "provided_by") or [None])[0] if record else None
        domain_node = (graph.out(record, "uses_telemetry_domain") or [None])[0] if record else None
        group = group_node.split(":", 1)[1] if group_node else None
        domain = domain_node.split(":", 1)[1] if domain_node else None
        described[node.id] = {
            "origin": "external" if external else "native",
            "family": attributes["family"],
            "supports": bool(graph.out(node.id, "supports")),
            "server_authoritative": not external,
            "challenge_derived": challenge is not None,
            "relationship_derived": attributes["family"] == "relationship",
            "external_record": record,
            "challenge": challenge,
            "provider_group": group,
            "telemetry_domain": domain if external else (SERVER_CHALLENGE if challenge else SERVER_BEHAVIOR),
            "depends_on": graph.out(node.id, "depends_on"),
            "partner": (graph.out(node.id, "names_partner") or [None])[0],
        }
        if graph.recipe == GRAPH_V2:
            described[node.id]["authenticity"] = graph.get(record).attributes["authenticity"] if record else None
            described[node.id]["provider_key"] = (graph.out(record, "authenticated_by") or [None])[0] if record else None
    supporting = [row for row in described.values() if row["supports"]]
    sources = sorted({graph.get(row["external_record"]).attributes["source_class"] for row in supporting if row["external_record"]})

    def shared(kind: str, key: str) -> list[dict]:
        groups: dict[str, list[str]] = {}
        for name, row in described.items():
            values = row[key] if isinstance(row[key], list) else ([row[key]] if row[key] else [])
            for value in values:
                groups.setdefault(value, []).append(name)
        return [{"kind": kind, "value": value, "observations": sorted(names)} for value, names in sorted(groups.items()) if len(names) > 1]

    common = (
        shared("provider_group", "provider_group") + shared("telemetry_domain", "telemetry_domain") + shared("external_record", "external_record")
        + shared("challenge", "challenge") + shared("dependency", "depends_on") + shared("partner", "partner")
        + (shared("provider_key", "provider_key") if graph.recipe == GRAPH_V2 else [])
    )
    for item in common:
        if item["kind"] == "provider_group":
            records = {described[name]["external_record"] for name in item["observations"]}
            item["providers"] = sorted({graph.get(record).attributes["provider"] for record in records})
    independence = {
        "native_families": sorted({row["family"] for row in supporting if row["origin"] == "native"}),
        "external_sources": sources,
        "provider_groups": sorted({row["provider_group"] for row in supporting if row["provider_group"]}),
        "telemetry_domains": sorted({row["telemetry_domain"] for row in supporting}),
        "supporting": len(supporting),
        "context_only": len(described) - len(supporting),
    }
    if graph.recipe == GRAPH_V2:
        # Who signed the supporting external evidence. Facts, not weight: nothing here changes a decision.
        states: dict[str, int] = {}
        for row in supporting:
            if row["authenticity"]:
                states[row["authenticity"]] = states.get(row["authenticity"], 0) + 1
        independence["authentication"] = dict(sorted(states.items()))
        independence["authenticated_provider_groups"] = sorted({row["provider_group"] for row in supporting if row["authenticity"] == "verified"})
    return {"observations": described, "independence": independence, "shared": common}


def graph_block(case: Mapping, recipe: str = GRAPH_RECIPE) -> dict:
    """``case["evidence"]["graph"]``: the graph, its digest, and its description (not part of the digest)."""
    graph = build_graph(case, recipe)
    graph.check_acyclic()
    return {**graph.to_dict(), "summary": describe(graph)}


def verify_graph(case: Mapping) -> list[str]:
    """Does a serialized case's graph hold together? Empty when it does.

    It reads the stored graph strictly (types, relations, endpoints, no duplicates, no derivation cycle,
    its digest), recomputes every observation id from the observation's own contents, rebuilds the graph
    from the case and compares, then checks each relationship against the evidence and provenance it
    came from. It needs no raw input, and it does not trust any stored id.
    """
    from .evidence import Observation

    evidence = case.get("evidence") if isinstance(case, Mapping) else None
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("graph"), Mapping):
        return ["the evidence has no graph"]
    block = evidence["graph"]
    try:
        stored = EvidenceGraph.from_dict({key: value for key, value in block.items() if key != "summary"})
    except GraphError as error:
        return [f"the graph is not well formed: {error}"]
    problems: list[str] = []
    rows = evidence.get("observations") or []
    for obs in rows:
        try:
            rebuilt = Observation(
                family=obs["family"], kind=obs["kind"], role=obs["role"], subject_id=obs["subject_id"], key=obs["key"],
                match_ids=tuple(obs["match_ids"]), evidence=obs["evidence"], depends_on=tuple(obs["depends_on"]),
                context=obs.get("context") or {}, source=obs["source"],
            )
        except (KeyError, TypeError, ValueError) as error:
            problems.append(f"observation {obs.get('observation_id')}: {error}")
            continue
        if rebuilt.observation_id != obs.get("observation_id"):
            problems.append(f"observation {obs.get('observation_id')} does not match its own contents")
    if problems:
        return problems
    try:
        expected = build_graph(case, stored.recipe)
        expected.check_acyclic()
    except (GraphError, KeyError, TypeError) as error:
        return [f"the case's evidence does not make a graph: {error!r}"]
    if stored.digest() != expected.digest():
        missing = {node.id for node in expected.nodes} - {node.id for node in stored.nodes}
        extra = {node.id for node in stored.nodes} - {node.id for node in expected.nodes}
        lost = {edge.key for edge in expected.edges} - {edge.key for edge in stored.edges}
        added = {edge.key for edge in stored.edges} - {edge.key for edge in expected.edges}
        problems.append(
            f"the graph does not match the case's evidence: {len(missing)} nodes missing, {len(extra)} extra, "
            f"{len(lost)} edges missing, {len(added)} extra, or attributes differ"
        )
    problems += _relationship_problems(case, stored)
    if block.get("summary") != describe(stored):
        problems.append("the graph's summary is not what its nodes and edges say")
    return problems


def _relationship_problems(case: Mapping, graph: EvidenceGraph) -> list[str]:
    """Each relationship, checked against the evidence and provenance it stands for."""
    problems = []
    evidence = case["evidence"]
    provenance = evidence.get("provenance") or {}
    rows = {f"observation:{obs['observation_id']}": obs for obs in evidence["observations"]}
    local = {node.id for node in graph.of_type("observation") if node.attributes.get("in_case")}
    if local != set(rows):
        problems.append("the graph's observations are not the case's")
    for name, obs in rows.items():
        targets = graph.out(name, "depends_on")
        if targets != sorted(f"observation:{dependency}" for dependency in obs["depends_on"]):
            problems.append(f"{name} depends_on does not match the observation")
        partners = graph.out(name, "names_partner")
        for target in targets:
            if target in local:
                if rows[target]["subject_id"] != obs["subject_id"]:
                    problems.append(f"{name} depends on an observation about another player")
            elif not partners or not set(graph.out(target, "about")) <= set(partners):
                problems.append(f"{name} depends on another case's observation that is not its partner's")
        derived = graph.out(name, "derived_from")
        if obs["family"] == "challenge":
            claim = obs["evidence"]["challenge"]
            if derived != [f"challenge:{claim['challenge_id']}"]:
                problems.append(f"{name} is not derived from its challenge")
            else:
                node = graph.get(derived[0]).attributes
                if {key: node[key] for key in ("origin", "type", "version", "commitment", "plan", "window")} != {
                    key: claim[key] for key in ("origin", "type", "version", "commitment", "plan", "window")
                }:
                    problems.append(f"{derived[0]} does not match the challenge evidence")
        elif obs["family"] == "external":
            claim = obs["evidence"]
            if derived != [f"external_record:{claim['external_id']}"]:
                problems.append(f"{name} is not derived from its external record")
            else:
                if graph.out(derived[0], "provided_by") != [f"provider_group:{claim['provider_group']}"]:
                    problems.append(f"{derived[0]} names the wrong provider group")
                if graph.out(derived[0], "uses_telemetry_domain") != [f"telemetry_domain:{claim['telemetry_domain']}"]:
                    problems.append(f"{derived[0]} names the wrong telemetry domain")
                if graph.recipe == GRAPH_V2:
                    auth = _authenticity(claim)
                    keys = graph.out(derived[0], "authenticated_by")
                    if auth["status"] != "verified" and keys:
                        problems.append(f"{derived[0]} is shown as signed, and its evidence says {auth['status']}")
                    if auth["status"] == "verified":
                        expected_key = f"provider_key:{auth['provider']}/{auth['key_id']}"
                        node = graph.get(expected_key)
                        if keys != [expected_key] or node is None or node.attributes.get("registry") != auth.get("registry"):
                            problems.append(f"{derived[0]} is not authenticated by the key its evidence names")
        elif derived:
            problems.append(f"{name} claims to be derived from something its family never is")
        if obs["family"] == "relationship" and partners != [f"player:{obs['evidence']['partner']}"]:
            problems.append(f"{name} does not name its partner")
        for target in partners:
            if graph.get(target) is None:
                problems.append(f"{name} names a partner that is not in the graph")
    external = provenance.get("external") if isinstance(provenance.get("external"), Mapping) else None
    records = graph.of_type("external_record")
    if external is not None:
        expected = 0 if external.get("mode") == "none" else external.get("records")
        if len(records) != expected:
            problems.append(f"the graph has {len(records)} external records, and provenance says {expected}")
    for node in graph.of_type("cohort"):
        if node.id != f"cohort:{(provenance.get('cohort') or {}).get('digest')}":
            problems.append("the graph's cohort is not the one in provenance")
    for node in graph.of_type("history"):
        if node.id != f"history:{(provenance.get('history') or {}).get('digest')}":
            problems.append("the graph's history is not the one in provenance")
    return problems


def verify_graphs(cases: Iterable[Mapping]) -> list[str]:
    """Across one run's cases: every observation a case depends on from another case exists on that case,
    about that player. A case alone cannot show this."""
    by_player = {case.get("player_id"): case for case in cases}
    known = {
        pid: {obs["observation_id"] for obs in (case.get("evidence") or {}).get("observations") or []}
        for pid, case in by_player.items()
    }
    problems = []
    for pid, case in sorted(by_player.items(), key=lambda item: str(item[0])):
        nodes = ((case.get("evidence") or {}).get("graph") or {}).get("nodes") or []
        edges = ((case.get("evidence") or {}).get("graph") or {}).get("edges") or []
        about = {}
        for edge in edges:
            if edge["relation"] == "about":
                about.setdefault(edge["source"], []).append(edge["target"])
        for node in nodes:
            if node["type"] != "observation" or node["attributes"].get("in_case"):
                continue
            owners = [target[len("player:"):] for target in about.get(node["id"], [])]
            if not owners or not all(node["id"][len("observation:"):] in known.get(owner, set()) for owner in owners):
                problems.append(f"{pid}: {node['id']} is not on the case of the player it is said to be about")
    return problems
