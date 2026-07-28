"""Task planner: decompose intents (including composite NL) into Task sequences."""

from __future__ import annotations

import re
from typing import List

from adaptive_mind_2501.models import Intent, Task

_NAVIGATE_WORDS = ('vai', 'raggiungi', 'spostati', 'dirigiti')
_PICK_WORDS = ('prendi', 'raccogli', 'afferra')
_LOCATION_HINTS = ('cucina', 'stanza', 'salone', 'soggiorno', 'corridoio', 'lab', 'ufficio')

_SPLIT_RE = re.compile(r'\s+e\s+|\s+poi\s+', flags=re.IGNORECASE)
_NAV_PREFIX_RE = re.compile(
    r'^(?:vai|raggiungi|spostati|dirigiti)\s+'
    r'(?:alla|allo|alle|agli|nella|nello|nelle|sugli|verso|nel|sul|su|ad|in|a)?\s*',
    flags=re.IGNORECASE,
)
_PICK_PREFIX_RE = re.compile(
    r'^(?:prendi|raccogli|afferra)\s+(?:il|lo|la|i|gli|le|un|una|uno|l[\'\u2019])?\s*',
    flags=re.IGNORECASE,
)


class TaskPlanner:
    """Dynamic decomposition of macro-objectives into executable tasks."""

    def plan(self, intent: Intent) -> List[Task]:
        raw = ''
        if isinstance(getattr(intent, 'parameters', None), dict):
            raw = intent.parameters.get('raw_text', '') or intent.parameters.get('target', '')
        if not raw:
            raw = getattr(intent, 'action', '') or ''
        raw = str(raw).strip()

        # Explicit macro-actions from brain / proactive engine
        if intent.action in {'emergency_stop', 'stop'}:
            return [Task(task_id='t1', action='emergency_stop', target='all')]
        if intent.action in {'dock_and_recharge', 'idle_patrol', 'resume_pending_goal'}:
            target = (
                intent.parameters.get('target')
                or intent.parameters.get('location')
                or intent.action
            )
            return [Task(task_id='t1', action=intent.action, target=str(target))]

        parts = [p.strip() for p in _SPLIT_RE.split(raw) if p and p.strip()]
        if not parts:
            parts = [raw] if raw else ['unknown']

        tasks: List[Task] = []
        for idx, part in enumerate(parts, start=1):
            task_id = f't{idx}'
            lowered = part.lower()

            if self._is_navigate(lowered):
                target = _NAV_PREFIX_RE.sub('', part).strip() or part
                tasks.append(Task(task_id=task_id, action='go_to', target=target))
            elif self._is_pick(lowered):
                target = _PICK_PREFIX_RE.sub('', part).strip() or part
                tasks.append(Task(task_id=task_id, action='pick_up', target=target))
            else:
                # Single-clause fallback using top-level intent.action
                if len(parts) == 1 and intent.action == 'navigate':
                    target = _NAV_PREFIX_RE.sub('', part).strip() or part
                    tasks.append(Task(task_id=task_id, action='go_to', target=target))
                elif len(parts) == 1 and intent.action == 'pick_up':
                    target = _PICK_PREFIX_RE.sub('', part).strip() or part
                    tasks.append(Task(task_id=task_id, action='pick_up', target=target))
                elif len(parts) == 1 and intent.action and intent.action != 'unknown':
                    tasks.append(
                        Task(task_id=task_id, action=intent.action, target=part)
                    )
                else:
                    tasks.append(Task(task_id=task_id, action='execute', target=part))

        return tasks

    @staticmethod
    def _is_navigate(text: str) -> bool:
        if any(w in text for w in _NAVIGATE_WORDS):
            return True
        # Bare location fragments after a split ("in cucina")
        return any(h in text for h in _LOCATION_HINTS) and not any(
            w in text for w in _PICK_WORDS
        )

    @staticmethod
    def _is_pick(text: str) -> bool:
        return any(w in text for w in _PICK_WORDS)
