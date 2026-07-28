"""Semantic graph memory for Adaptive Mind 2501 (NetworkX DiGraph + JSON)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import networkx as nx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'networkx is required for GraphMemory. Install with: pip install networkx'
    ) from exc

logger = logging.getLogger(__name__)

CONTEXT_NODE = '__context__'


class GraphMemory:
    """In-memory directed semantic graph with optional JSON persistence.

    Works fully offline with no ROS dependencies.
    """

    def __init__(self, persist_path: Optional[str] = None, **kwargs: Any) -> None:
        # Backward-compatible alias used by brain_node / older callers
        if persist_path is None and 'storage_path' in kwargs:
            persist_path = kwargs.pop('storage_path')
        self.persist_path: Optional[str] = persist_path
        self.graph: nx.DiGraph = nx.DiGraph()
        if self.persist_path:
            self.load_from_json(self.persist_path)

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def add_fact(
        self,
        source: str,
        target: str,
        relation: str,
        attributes: Optional[dict] = None,
    ) -> None:
        """Add a directed edge ``source -[relation]-> target`` with optional attrs."""
        if not source or not target or not relation:
            raise ValueError('source, target and relation must be non-empty strings')

        attrs = dict(attributes or {})
        attrs['relation'] = relation
        attrs.setdefault('created_at', time.time())

        self.graph.add_node(source)
        self.graph.add_node(target)

        if self.graph.has_edge(source, target):
            existing = dict(self.graph.edges[source, target])
            relations = list(existing.get('relations', []))
            prev = existing.get('relation')
            if prev and prev not in relations:
                relations.append(prev)
            if relation not in relations:
                relations.append(relation)
            existing.update(attrs)
            existing['relations'] = relations
            existing['relation'] = relation
            existing['updated_at'] = time.time()
            self.graph.edges[source, target].clear()
            self.graph.edges[source, target].update(existing)
        else:
            self.graph.add_edge(source, target, **attrs)

    def update_context(self, context_data: dict) -> None:
        """Merge ``context_data`` into a dedicated context node and fact edges."""
        if not isinstance(context_data, dict):
            raise TypeError('context_data must be a dict')

        if CONTEXT_NODE not in self.graph:
            self.graph.add_node(CONTEXT_NODE, kind='context', created_at=time.time())

        node_attrs = self.graph.nodes[CONTEXT_NODE]
        for key, value in context_data.items():
            node_attrs[key] = value
        node_attrs['kind'] = 'context'
        node_attrs['updated_at'] = time.time()

        for key, value in context_data.items():
            value_id = f'ctx:{key}'
            self.graph.add_node(
                value_id,
                kind='context_value',
                key=key,
                value=value,
                updated_at=time.time(),
            )
            self.add_fact(
                CONTEXT_NODE,
                value_id,
                'has_context',
                attributes={'key': key, 'value': value},
            )

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def query_related(
        self,
        entity: str,
        relation: Optional[str] = None,
    ) -> list:
        """Return related facts for ``entity``, optionally filtered by relation."""
        if entity not in self.graph:
            return []

        results: List[Dict[str, Any]] = []

        for src, tgt, data in self.graph.out_edges(entity, data=True):
            if self._relation_matches(data, relation):
                results.append(self._fact_record(src, tgt, data, direction='out'))

        for src, tgt, data in self.graph.in_edges(entity, data=True):
            if self._relation_matches(data, relation):
                results.append(self._fact_record(src, tgt, data, direction='in'))

        return results

    @staticmethod
    def _relation_matches(data: dict, relation: Optional[str]) -> bool:
        if relation is None:
            return True
        if data.get('relation') == relation:
            return True
        return relation in list(data.get('relations') or [])

    @staticmethod
    def _fact_record(
        source: str,
        target: str,
        data: dict,
        direction: str,
    ) -> Dict[str, Any]:
        record = {
            'source': source,
            'target': target,
            'relation': data.get('relation'),
            'direction': direction,
            'attributes': {
                k: v for k, v in data.items()
                if k not in {'relation', 'relations'}
            },
        }
        if 'relations' in data:
            record['relations'] = list(data['relations'])
        return record

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save_to_json(self, filepath: Optional[str] = None) -> None:
        """Serialize nodes and edges to JSON."""
        path = self._resolve_path(filepath)
        if path is None:
            raise ValueError('No filepath provided and persist_path is not set')

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'nodes': [
                {'id': node_id, **dict(attrs)}
                for node_id, attrs in self.graph.nodes(data=True)
            ],
            'edges': [
                {
                    'source': src,
                    'target': tgt,
                    **dict(attrs),
                }
                for src, tgt, attrs in self.graph.edges(data=True)
            ],
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding='utf-8',
        )
        logger.info('GraphMemory saved to %s', path)

    def load_from_json(self, filepath: Optional[str] = None) -> None:
        """Deserialize graph from JSON if the file exists; no-op / empty on miss."""
        path = self._resolve_path(filepath)
        if path is None or not path.exists():
            logger.debug('GraphMemory load skipped (missing file): %s', path)
            return

        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning('GraphMemory load failed for %s: %s', path, exc)
            self.graph = nx.DiGraph()
            return

        # Prefer custom {nodes, edges} schema; fall back to NetworkX node-link
        if isinstance(payload, dict) and 'edges' in payload and 'nodes' in payload:
            graph = nx.DiGraph()
            for node in payload.get('nodes', []):
                if not isinstance(node, dict) or 'id' not in node:
                    continue
                node_id = node['id']
                attrs = {k: v for k, v in node.items() if k != 'id'}
                graph.add_node(node_id, **attrs)
            for edge in payload.get('edges', []):
                if not isinstance(edge, dict):
                    continue
                src = edge.get('source')
                tgt = edge.get('target')
                if src is None or tgt is None:
                    continue
                attrs = {
                    k: v for k, v in edge.items() if k not in {'source', 'target'}
                }
                graph.add_edge(src, tgt, **attrs)
            self.graph = graph
            logger.info('GraphMemory loaded from %s', path)
            return

        try:
            self.graph = nx.node_link_graph(payload)
            logger.info('GraphMemory loaded (node-link) from %s', path)
        except Exception as exc:  # noqa: BLE001
            logger.warning('GraphMemory load failed for %s: %s', path, exc)
            self.graph = nx.DiGraph()

    def _resolve_path(self, filepath: Optional[str]) -> Optional[Path]:
        chosen = filepath if filepath is not None else self.persist_path
        return Path(chosen) if chosen else None

    # ------------------------------------------------------------------ #
    # Compatibility helpers
    # ------------------------------------------------------------------ #
    def save(self, filepath: Optional[str] = None) -> None:
        """Alias for :meth:`save_to_json`."""
        self.save_to_json(filepath)

    def load(self, filepath: Optional[str] = None) -> None:
        """Alias for :meth:`load_from_json`."""
        self.load_from_json(filepath)

    @property
    def storage_path(self) -> Optional[str]:
        return self.persist_path

    def stats(self) -> Dict[str, int]:
        return {
            'nodes': self.graph.number_of_nodes(),
            'edges': self.graph.number_of_edges(),
        }
