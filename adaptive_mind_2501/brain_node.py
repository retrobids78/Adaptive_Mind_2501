"""Adaptive Mind 2501 — ROS 2 brain node with standalone/mock fallback.

Wires DialogueParser, GraphMemory, TaskPlanner, SafetyGovernor and
ProactiveEngine into a single cognitive pipeline.

Topics (ROS or mock):
  - sub  /adaptive_mind/user_command   (std_msgs/String)
  - sub  /adaptive_mind/battery        (std_msgs/String)  optional
  - pub  /adaptive_mind/task_queue     (std_msgs/String JSON)
  - pub  /adaptive_mind/status         (std_msgs/String JSON)
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any, Dict, List, Optional

from adaptive_mind_2501.dialogue_parser.parser import DialogueParser
from adaptive_mind_2501.knowledge_engine.graph_memory import GraphMemory
from adaptive_mind_2501.models import Intent
from adaptive_mind_2501.proactive_engine.trigger import ProactiveEngine
from adaptive_mind_2501.safety_governor.rules import SafetyGovernor
from adaptive_mind_2501.task_planner.planner import TaskPlanner

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('adaptive_mind_2501.brain')

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False
    rclpy = None  # type: ignore[assignment]
    Node = object  # type: ignore[misc, assignment]
    String = None  # type: ignore[assignment]
    logger.warning(
        'rclpy not found — Standalone/Mock mode '
        '(pipeline attiva, topic simulati a terminale)'
    )

TOPIC_USER_COMMAND = '/adaptive_mind/user_command'
TOPIC_TASK_QUEUE = '/adaptive_mind/task_queue'
TOPIC_STATUS = '/adaptive_mind/status'
TOPIC_BATTERY = '/adaptive_mind/battery'

DEFAULT_PARAMS: Dict[str, Any] = {
    'ollama_url': 'http://localhost:11434/api/generate',
    'ollama_model': 'llama3',
    'graph_path': '/tmp/adaptive_mind_2501_graph.json',
    'proactive_tick_period': 15.0,
    'battery_low_threshold': 20.0,
    'idle_timeout_sec': 30.0,
    'status_period': 2.0,
}


class CognitiveBrain:
    """Shared cognitive pipeline used by ROS and Standalone modes."""

    def _init_cognition(self, params: Dict[str, Any]) -> None:
        self.params = dict(params)
        self.parser = DialogueParser(
            ollama_url=params.get('ollama_url'),
            model=params.get('ollama_model'),
        )
        self.memory = GraphMemory(persist_path=params.get('graph_path'))
        self.planner = TaskPlanner()
        self.governor = SafetyGovernor(emergency_stop=False)
        self.proactive = ProactiveEngine(
            context_source=self.memory,
            battery_low_threshold=float(params.get('battery_low_threshold', 20.0)),
            idle_timeout_sec=float(params.get('idle_timeout_sec', 30.0)),
        )
        self.is_idle = True
        self.battery_level = 100.0
        self._last_command_ts = time.time()
        self.standalone = not ROS_AVAILABLE
        self.mode = 'standalone' if self.standalone else 'ros2'

    # ------------------------------------------------------------------ #
    # Logging helpers
    # ------------------------------------------------------------------ #
    def _log_info(self, message: str) -> None:
        if ROS_AVAILABLE and hasattr(self, 'get_logger'):
            self.get_logger().info(message)  # type: ignore[attr-defined]
        else:
            logger.info(message)

    def _log_warn(self, message: str) -> None:
        if ROS_AVAILABLE and hasattr(self, 'get_logger'):
            self.get_logger().warn(message)  # type: ignore[attr-defined]
        else:
            logger.warning(message)

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def process_command(self, text: str) -> List[dict]:
        """Parse → memory → plan → safety → publish approved tasks."""
        text = '' if text is None else str(text).strip()
        self._log_info(f'[{TOPIC_USER_COMMAND}] {text!r}')
        if not text:
            self._publish_status('pipeline', {
                'intent': None,
                'approved': [],
                'mode': self.mode,
                'note': 'empty_command',
            })
            return []

        self.is_idle = False
        self._last_command_ts = time.time()

        intent = self.parser.parse(text)
        self._apply_stop_semantics(text, intent)
        try:
            self.memory.add_fact('User', intent.action, 'requested')
            self.memory.update_context({
                'last_command': text,
                'last_intent': intent.action,
                'battery_level': self.battery_level,
            })
        except Exception as exc:  # noqa: BLE001
            self._log_warn(f'GraphMemory update failed: {exc}')

        try:
            tasks = self.planner.plan(intent)
        except Exception as exc:  # noqa: BLE001
            self._log_warn(f'TaskPlanner failed: {exc}')
            tasks = []

        approved = self._approve_tasks(tasks, source='user')
        self._persist_memory()
        self._publish_status('pipeline', {
            'intent': {
                'name': intent.name,
                'action': intent.action,
                'parameters': intent.parameters,
            },
            'approved': [t['task_id'] for t in approved],
            'mode': self.mode,
        })
        return approved

    def tick_proactive(self) -> List[dict]:
        """Evaluate proactive triggers and push approved tasks."""
        idle_seconds = max(0.0, time.time() - self._last_command_ts)
        if idle_seconds >= float(self.params.get('idle_timeout_sec', 30.0)):
            self.is_idle = True

        suggestions = self.proactive.evaluate({
            'is_idle': self.is_idle,
            'battery_level': self.battery_level,
            'idle_seconds': idle_seconds,
            'emergency_stop': self.governor.emergency_stop,
        })
        if not suggestions:
            return []

        proactive_tasks: List[dict] = []
        for suggestion in suggestions:
            self._log_info(
                f'Proactive suggestion: {suggestion.get("goal")} '
                f'({suggestion.get("reason")})'
            )
            raw = suggestion.get('raw_text') or suggestion.get('goal', '')
            intent = self.parser.parse(str(raw))
            if intent.action == 'unknown' and suggestion.get('goal'):
                intent = Intent(
                    name='proactive_intent',
                    action=str(suggestion['goal']),
                    parameters={
                        'raw_text': raw,
                        **{
                            k: v for k, v in suggestion.items()
                            if k != 'raw_text'
                        },
                    },
                )
            tasks = self.planner.plan(intent)
            approved = self._approve_tasks(
                tasks,
                source='proactive',
                extra={
                    'proactive_goal': suggestion.get('goal'),
                    'proactive_reason': suggestion.get('reason'),
                },
            )
            proactive_tasks.extend(approved)

        self._persist_memory()
        return proactive_tasks

    def set_battery(self, raw: str) -> None:
        try:
            self.battery_level = float(str(raw).strip())
            return
        except ValueError:
            pass
        try:
            payload = json.loads(raw)
            self.battery_level = float(
                payload.get('percent', payload.get('battery_level', self.battery_level))
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            self._log_warn(f'Invalid battery payload on {TOPIC_BATTERY}: {raw!r}')

    def heartbeat(self) -> None:
        self._publish_status('heartbeat', {
            'battery_level': self.battery_level,
            'is_idle': self.is_idle,
            'emergency_stop': self.governor.emergency_stop,
            'memory': self.memory.stats(),
            'mode': self.mode,
        })

    def _approve_tasks(
        self,
        tasks: list,
        source: str,
        extra: Optional[dict] = None,
    ) -> List[dict]:
        approved: List[dict] = []
        for task in tasks:
            ok, reason = self.governor.validate(task)
            if not ok:
                self._log_warn(
                    f'Safety rejected {task.task_id}/{task.action}: {reason}'
                )
                continue
            item = {
                'task_id': task.task_id,
                'action': task.action,
                'target': task.target,
                'status': 'approved',
            }
            if extra:
                item.update(extra)
            approved.append(item)

        if approved:
            payload = {
                'source': source,
                'timestamp': time.time(),
                'tasks': approved,
            }
            self._publish_task_queue(json.dumps(payload, ensure_ascii=False))
            self._log_info(
                f'[{TOPIC_TASK_QUEUE}] {len(approved)} task(s) from {source}: '
                f'{[t["action"] for t in approved]}'
            )
        return approved

    def _apply_stop_semantics(self, text: str, intent: Intent) -> None:
        lowered = text.lower()
        # Word-boundary match avoids false positives (e.g. "conferma")
        stop_re = re.compile(
            r'(?<!\w)(stop|ferma|fermati|halt|emergenza|abort|annulla)(?!\w)',
            re.IGNORECASE,
        )
        if intent.action == 'emergency_stop' or stop_re.search(lowered):
            self.governor.set_emergency_stop(True)
            intent.action = 'emergency_stop'
            intent.parameters = {
                **(intent.parameters or {}),
                'raw_text': text,
            }
        elif intent.action != 'unknown' and self.governor.emergency_stop:
            self.governor.set_emergency_stop(False)

    def _persist_memory(self) -> None:
        try:
            self.memory.save_to_json()
        except (OSError, ValueError) as exc:
            self._log_warn(f'GraphMemory persist skipped: {exc}')

    # ------------------------------------------------------------------ #
    # Transport hooks (ROS or mock)
    # ------------------------------------------------------------------ #
    def _publish_task_queue(self, encoded: str) -> None:
        raise NotImplementedError

    def _publish_status(self, state: str, details: Optional[dict] = None) -> None:
        payload = {
            'state': state,
            'timestamp': time.time(),
            'details': details or {},
        }
        self._publish_status_raw(json.dumps(payload, ensure_ascii=False))

    def _publish_status_raw(self, encoded: str) -> None:
        raise NotImplementedError


if ROS_AVAILABLE:

    class AdaptiveMindBrain(Node, CognitiveBrain):
        """ROS 2 Humble node orchestrating the five cognitive modules."""

        def __init__(self, **overrides: Any) -> None:
            Node.__init__(self, 'adaptive_mind_brain')
            params = self._load_ros_params(overrides)
            self._init_cognition(params)

            self.sub_cmd = self.create_subscription(
                String, TOPIC_USER_COMMAND, self._on_user_command, 10,
            )
            self.sub_battery = self.create_subscription(
                String, TOPIC_BATTERY, self._on_battery, 10,
            )
            self.pub_tasks = self.create_publisher(String, TOPIC_TASK_QUEUE, 10)
            self.pub_status = self.create_publisher(String, TOPIC_STATUS, 10)

            tick = float(params.get('proactive_tick_period', 15.0))
            status_period = float(params.get('status_period', 2.0))
            self.create_timer(tick, self._on_proactive_tick)
            self.create_timer(status_period, self.heartbeat)

            self._log_info(
                'Progetto 2501 brain online '
                f'(mode=ros2, graph={params.get("graph_path")}, '
                f'proactive_tick={tick}s)'
            )
            self._publish_status('online', {'modules': 5, 'mode': 'ros2'})

        def _load_ros_params(self, overrides: dict) -> Dict[str, Any]:
            merged = dict(DEFAULT_PARAMS)
            merged.update(overrides)
            for key, default in DEFAULT_PARAMS.items():
                self.declare_parameter(key, default)
                merged[key] = self.get_parameter(key).value
            # Allow explicit kwargs to win (tests)
            merged.update(overrides)
            return merged

        def _on_user_command(self, msg: Any) -> None:
            self.process_command(msg.data)

        def _on_battery(self, msg: Any) -> None:
            self.set_battery(msg.data)

        def _on_proactive_tick(self) -> None:
            self.tick_proactive()

        def _publish_task_queue(self, encoded: str) -> None:
            msg = String()
            msg.data = encoded
            self.pub_tasks.publish(msg)

        def _publish_status_raw(self, encoded: str) -> None:
            msg = String()
            msg.data = encoded
            self.pub_status.publish(msg)

else:

    class AdaptiveMindBrain(CognitiveBrain):
        """Standalone/Mock brain — same pipeline, topics logged to terminal."""

        def __init__(self, **overrides: Any) -> None:
            params = dict(DEFAULT_PARAMS)
            params.update(overrides)
            # Prefer deterministic offline parsing unless overridden
            if 'ollama_url' not in overrides and 'ollama_model' not in overrides:
                params.setdefault('ollama_url', None)
                params.setdefault('ollama_model', None)
            self._init_cognition(params)
            self._log_info(
                'Progetto 2501 brain online '
                f'(mode=standalone, graph={params.get("graph_path")})'
            )
            self._publish_status('online', {'modules': 5, 'mode': 'standalone'})

        def _publish_task_queue(self, encoded: str) -> None:
            logger.info('[MOCK %s] %s', TOPIC_TASK_QUEUE, encoded)

        def _publish_status_raw(self, encoded: str) -> None:
            logger.info('[MOCK %s] %s', TOPIC_STATUS, encoded)

        def destroy_node(self) -> None:
            return None


def _run_standalone_repl(brain: AdaptiveMindBrain) -> None:
    print()
    print('=' * 64)
    print('  Progetto 2501 — Adaptive Mind (Standalone / Mock)')
    print(f'  Topics: {TOPIC_USER_COMMAND} → pipeline → {TOPIC_TASK_QUEUE}')
    print('  Commands: <NL> | battery <%> | tick | status | quit')
    print('=' * 64)
    print()
    while True:
        try:
            line = input('2501>>> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        lowered = line.lower()
        if lowered in {'quit', 'exit', 'q'}:
            break
        if lowered in {'tick', 'proactive'}:
            brain.tick_proactive()
            continue
        if lowered in {'status', 'heartbeat'}:
            brain.heartbeat()
            continue
        if lowered.startswith('battery '):
            brain.set_battery(line.split(None, 1)[1])
            brain._log_info(f'Battery → {brain.battery_level}%')
            continue
        brain.process_command(line)


def main(args=None) -> None:
    if ROS_AVAILABLE:
        assert rclpy is not None
        rclpy.init(args=args)
        node = AdaptiveMindBrain()
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                node.memory.save_to_json()
            except Exception:  # noqa: BLE001
                pass
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        return

    logger.info('Avvio Standalone/Mock (rclpy assente)')
    brain = AdaptiveMindBrain()
    cli = None
    if args:
        cli = ' '.join(args)
    elif len(sys.argv) > 1:
        cli = ' '.join(sys.argv[1:])
    try:
        if cli:
            brain.process_command(cli)
            brain.heartbeat()
        else:
            _run_standalone_repl(brain)
    finally:
        try:
            brain.memory.save_to_json()
        except Exception:  # noqa: BLE001
            pass
        brain.destroy_node()
        logger.info('Adaptive Mind shutdown')


if __name__ == '__main__':
    main()
