"""Proactive engine: context-driven goal suggestions for Adaptive Mind 2501.

Works fully offline with no ROS dependencies. Optionally reads from a
GraphMemory instance passed as ``context_source``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_BATTERY_LOW = 20.0
DEFAULT_IDLE_TIMEOUT_SEC = 30.0


class ProactiveEngine:
    """Evaluate runtime context and emit proactive goals / suggestions."""

    def __init__(
        self,
        context_source: Any = None,
        *,
        battery_low_threshold: float = DEFAULT_BATTERY_LOW,
        idle_timeout_sec: float = DEFAULT_IDLE_TIMEOUT_SEC,
        min_suggestion_interval_sec: float = 5.0,
    ) -> None:
        self.context_source = context_source
        self.battery_low_threshold = float(battery_low_threshold)
        self.idle_timeout_sec = float(idle_timeout_sec)
        self.min_suggestion_interval_sec = float(min_suggestion_interval_sec)
        self.last_check = time.time()
        self._last_suggestion_ts = 0.0

    def evaluate(self, current_context: dict) -> list:
        """Check context triggers and return proactive suggestions / goals.

        Expected ``current_context`` keys (all optional):
          - battery / battery_level / battery_percent: float
          - is_idle: bool
          - idle_seconds / idle_timeout: float
          - emergency_stop: bool
          - pending_goals: list[str]
          - location / current_location: str

        Returns a list of suggestion dicts, each with at least:
          ``goal``, ``reason``, ``priority``, ``raw_text``.
        """
        ctx = dict(current_context or {})
        ctx = self._enrich_from_source(ctx)

        now = time.time()
        self.last_check = now

        if ctx.get('emergency_stop'):
            return []

        if now - self._last_suggestion_ts < self.min_suggestion_interval_sec:
            return []

        suggestions: List[Dict[str, Any]] = []

        battery = self._as_float(
            ctx.get('battery',
                    ctx.get('battery_level',
                            ctx.get('battery_percent', 100.0))),
            100.0,
        )
        if battery <= self.battery_low_threshold:
            suggestions.append({
                'goal': 'dock_and_recharge',
                'reason': 'battery_low',
                'priority': 'high',
                'battery': battery,
                'raw_text': 'vai in stazione di ricarica',
            })

        pending = ctx.get('pending_goals') or []
        if isinstance(pending, str):
            pending = [pending]
        pending = [p for p in pending if p]

        is_idle = bool(ctx.get('is_idle', False))
        idle_seconds = self._as_float(
            ctx.get('idle_seconds', ctx.get('idle_timeout', 0.0)),
            0.0,
        )
        # If caller only flags idle without duration, treat as timed-out
        if is_idle and idle_seconds <= 0.0:
            idle_seconds = self.idle_timeout_sec

        if pending and is_idle:
            target = pending[0]
            suggestions.append({
                'goal': 'resume_pending_goal',
                'reason': 'pending_goal',
                'priority': 'medium',
                'target': target,
                'raw_text': f'vai a {target}',
            })

        if (
            is_idle
            and idle_seconds >= self.idle_timeout_sec
            and not pending
            and not any(s['goal'] == 'dock_and_recharge' for s in suggestions)
        ):
            location = ctx.get('location') or ctx.get('current_location') or 'zona giorno'
            suggestions.append({
                'goal': 'idle_patrol',
                'reason': 'idle_timeout',
                'priority': 'low',
                'location': location,
                'raw_text': f'vai in {location}',
            })

        if suggestions:
            self._last_suggestion_ts = now
            logger.info(
                'ProactiveEngine suggested %d goal(s): %s',
                len(suggestions),
                [s['goal'] for s in suggestions],
            )
            if self.context_source is not None and hasattr(
                self.context_source, 'update_context'
            ):
                try:
                    self.context_source.update_context({
                        'last_proactive_goals': [s['goal'] for s in suggestions],
                        'last_proactive_ts': now,
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.debug('Could not update GraphMemory context: %s', exc)

        return suggestions

    def check_triggers(self, is_idle: bool, battery_level: float) -> list:
        """Backward-compatible helper returning raw NL strings for the brain."""
        suggestions = self.evaluate({
            'is_idle': is_idle,
            'battery_level': battery_level,
            'idle_seconds': self.idle_timeout_sec if is_idle else 0.0,
        })
        return [s.get('raw_text', s.get('goal', '')) for s in suggestions if s]

    def _enrich_from_source(self, ctx: dict) -> dict:
        """Merge GraphMemory (or similar) state into the evaluation context."""
        source = self.context_source
        if source is None:
            return ctx

        enriched = dict(ctx)

        if hasattr(source, 'query_related') and 'pending_goals' not in enriched:
            pending: List[str] = []
            # Prefer an explicit helper if present
            if hasattr(source, 'pending_goals') and callable(source.pending_goals):
                try:
                    pending = list(source.pending_goals())
                except Exception:  # noqa: BLE001
                    pending = []
            if not pending:
                try:
                    for fact in source.query_related('self', relation='has_goal'):
                        target = fact.get('target')
                        if target:
                            pending.append(str(target))
                except Exception:  # noqa: BLE001
                    pass
            if pending:
                enriched['pending_goals'] = pending

        if hasattr(source, 'graph') and '__context__' in getattr(source, 'graph', {}):
            try:
                node = dict(source.graph.nodes['__context__'])
                for key in ('battery', 'battery_level', 'location', 'current_location',
                            'emergency_stop', 'is_idle'):
                    if key not in enriched and key in node:
                        enriched[key] = node[key]
            except Exception:  # noqa: BLE001
                pass

        return enriched

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
