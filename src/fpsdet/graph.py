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
from collections.abc import Mapping
from dataclasses import dataclass

from .evidence import canonical_json

GRAPH_RECIPE = "fpsdet.graph/1"

# Every node type, and what a node of it stands for.
NODE_TYPES: dict[str, str] = {
    "case": "the case: one player's evidence and decision in one run",
    "player": "a player pseudonym: the case's subject, or a partner a relationship names",
    "match": "a match id",
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
    "played_in": (frozenset({"player"}), frozenset({"match"}), "a match the case's player played in the scored window"),
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


def node_id(node_type: str, key: str) -> str:
    """A node's id: its type, a colon, and its key. The type is fpsdet's; only the key comes from data."""
    if node_type not in NODE_TYPES:
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

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}
        # Each edge indexed from both ends, so a lookup costs the node's degree, not the graph's size.
        self._out: dict[tuple[str, str], set[str]] = {}
        self._in: dict[tuple[str, str], set[str]] = {}

    def node(self, node_type: str, key: str, **attributes) -> str:
        """Add a node, or find it. The same id with different attributes is a conflict, never a merge."""
        identity = node_id(node_type, key)
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
        if relation not in RELATIONS:
            raise GraphError(f"unknown relation {relation!r}")
        for end in (source, target):
            if end not in self._nodes:
                raise GraphError(f"{relation} names {end}, which is not a node")
        sources, targets, _meaning = RELATIONS[relation]
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
            "recipe": GRAPH_RECIPE,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def digest(self) -> str:
        """``fpsdet.graph/1``: SHA-256 over the recipe, a zero byte, and the canonical JSON of the nodes
        sorted by id and the edges sorted by source, relation and target."""
        body = canonical_json(self.material())
        return "sha256:" + hashlib.sha256(GRAPH_RECIPE.encode("ascii") + b"\0" + body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {**self.material(), "digest": self.digest()}

    @classmethod
    def from_dict(cls, obj) -> EvidenceGraph:
        """A serialized graph, checked: its recipe, every node's type and id, every edge's relation and
        endpoints, no node or edge listed twice, no forbidden cycle, and its digest."""
        if not isinstance(obj, Mapping) or obj.get("recipe") != GRAPH_RECIPE:
            raise GraphError(f"not a {GRAPH_RECIPE} graph")
        nodes, edges = obj.get("nodes"), obj.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise GraphError("nodes and edges must be lists")
        graph = cls()
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


def schema() -> dict:
    """The node types and relations, as data, for documentation and for a later reader."""
    return {
        "recipe": GRAPH_RECIPE,
        "node_types": dict(NODE_TYPES),
        "relations": {
            name: {"from": sorted(sources), "to": sorted(targets), "meaning": meaning, "acyclic": name in ACYCLIC}
            for name, (sources, targets, meaning) in RELATIONS.items()
        },
    }

