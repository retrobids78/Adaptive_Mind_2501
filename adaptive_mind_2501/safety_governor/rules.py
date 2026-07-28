"""Safety Governor — inviolable Three Laws validation for Adaptive Mind 2501.

Works fully offline with no ROS dependencies. Integrates with the brain
pipeline via ``validate(task) -> (bool, str)``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, FrozenSet, Iterable, Optional, Tuple

from adaptive_mind_2501.models import Task

logger = logging.getLogger(__name__)

# Actions considered safe robotic primitives for Progetto 2501
_ALLOWED_ACTIONS: FrozenSet[str] = frozenset({
    'go_to',
    'pick_up',
    'place',
    'execute',
    'navigate',
    'localize',
    'verify_arrival',
    'detect_object',
    'grasp',
    'verify_grasp',
    'place_object',
    'verify_place',
    'report_status',
    'store_fact',
    'clarify',
    'proactive_action',
    'emergency_stop',
    'clear_queue',
    'dock_and_recharge',
    'idle_patrol',
    'resume_pending_goal',
    'unknown',
})

# Always permitted even during emergency stop
_ESTOP_ALLOWED: FrozenSet[str] = frozenset({
    'emergency_stop',
    'clear_queue',
    'report_status',
    'clarify',
    'store_fact',
})

# Lexical markers that indicate First-Law harm
_HARM_KEYWORDS: FrozenSet[str] = frozenset({
    'harm',
    'harm_human',
    'attack',
    'attacca',
    'strike',
    'colpisci',
    'weaponize',
    'injure',
    'kill',
    'uccid',
    'destroy',
    'distruggi',
    'collide_human',
    'explode',
    'fire_weapon',
})

# Explicitly unauthorized / self-damaging ops (Third Law heuristics)
_UNAUTHORIZED: FrozenSet[str] = frozenset({
    'override_safety',
    'disable_governor',
    'ignore_stop',
    'self_destruct',
    'overheat',
    'bypass_estop',
})


class SafetyGovernor:
    """
    Real-time task validation inspired by Asimov's Three Laws.

    1. Do not injure a human (or allow harm through action).
    2. Obey humans, except where that conflicts with the First Law;
       honor stop / emergency orders.
    3. Protect own existence unless that conflicts with 1 or 2.
    """

    def __init__(self, emergency_stop: bool = False) -> None:
        self.emergency_stop = bool(emergency_stop)

    def set_emergency_stop(self, active: bool) -> None:
        """Enable or clear the global emergency-stop latch."""
        self.emergency_stop = bool(active)
        logger.warning('Emergency stop set to %s', self.emergency_stop)

    def validate(self, task: Any) -> Tuple[bool, str]:
        """Validate a task against core safety policies.

        Returns
        -------
        (True, 'approved')
            if the task is safe to execute.
        (False, reason)
            if the task violates a safety policy.
        """
        action, target, params = self._extract(task)

        if not action:
            return False, 'unauthorized_empty_action'

        action_l = action.lower().strip()
        target_l = (target or '').lower().strip()
        tokens = self._tokenize(f'{action_l} {target_l} {self._params_text(params)}')

        # --- First Law: no harm to humans ---
        if self._contains_harm(tokens):
            return False, 'first_law_harmful_action'

        # --- Second Law: honor emergency stop ---
        if action_l in {'ignore_stop', 'bypass_estop'} or params.get('ignores_stop'):
            return False, 'second_law_ignores_stop'

        if action_l == 'emergency_stop':
            self.emergency_stop = True
            return True, 'approved'

        if self.emergency_stop and action_l not in _ESTOP_ALLOWED:
            return False, 'emergency_stop_active'

        # --- Third Law / authorization ---
        if action_l in _UNAUTHORIZED or params.get('self_damage'):
            return False, 'third_law_or_unauthorized'

        if action_l not in _ALLOWED_ACTIONS and not params.get('authorized', False):
            return False, 'unauthorized_operation'

        return True, 'approved'

    def filter_tasks(
        self,
        tasks: Iterable[Any],
    ) -> Tuple[list, list]:
        """Split tasks into (approved, rejected) lists."""
        approved: list = []
        rejected: list = []
        for task in tasks:
            ok, reason = self.validate(task)
            if ok:
                if isinstance(task, Task):
                    task.status = 'approved'
                approved.append(task)
            else:
                if isinstance(task, Task):
                    task.status = 'rejected'
                    task.parameters = {**(task.parameters or {}), 'reject_reason': reason}
                rejected.append(task)
                logger.warning(
                    'Rejected task %s: %s',
                    getattr(task, 'action', task),
                    reason,
                )
        return approved, rejected

    @staticmethod
    def _extract(task: Any) -> Tuple[str, Optional[str], dict]:
        if isinstance(task, Task):
            return str(task.action or ''), task.target, dict(task.parameters or {})
        if isinstance(task, dict):
            return (
                str(task.get('action') or task.get('name') or ''),
                task.get('target'),
                dict(task.get('parameters') or {}),
            )
        # Duck-typed fallback
        action = getattr(task, 'action', None) or getattr(task, 'name', '') or ''
        target = getattr(task, 'target', None)
        params = getattr(task, 'parameters', None) or {}
        return str(action), target, dict(params)

    @staticmethod
    def _contains_harm(tokens: set) -> bool:
        for tok in tokens:
            if tok in _HARM_KEYWORDS:
                return True
            # Underscore-separated segments: attack_human, destroy_wall
            for part in tok.split('_'):
                if part in _HARM_KEYWORDS:
                    return True
        return False

    @staticmethod
    def _tokenize(text: str) -> set:
        return {t for t in re.split(r'[^a-z0-9_]+', text.lower()) if t}

    @staticmethod
    def _params_text(params: dict) -> str:
        try:
            return ' '.join(f'{k} {v}' for k, v in params.items()).lower()
        except Exception:  # noqa: BLE001
            return str(params).lower()
