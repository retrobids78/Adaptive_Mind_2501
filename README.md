# Project 2501

**Project 2501** (*Venticinque Zero Uno*) — modular, proactive cognitive control
for autonomous robotics on **ROS 2 Humble**.

Inspired by *Project 2501* (the Puppet Master) from *Ghost in the Shell*: an
adaptive cybernetic entity that learns context and acts with autonomy — gated
by an inviolable Safety Governor.

[![ROS 2](https://img.shields.io/badge/ROS%202-Humble-blue)](https://docs.ros.org/en/humble/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-yellow)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green)](LICENSE)
[![Package](https://img.shields.io/badge/ament-adaptive__mind__2501-lightgrey)](package.xml)
[![Mode](https://img.shields.io/badge/mode-ROS%20%7C%20Standalone-orange)](adaptive_mind_2501/brain_node.py)

> ROS package id: `adaptive_mind_2501` · Display name: **Project 2501**

---

## Architecture

Five cognitive modules orchestrated by a single brain node:

| Module | Path | Role |
|--------|------|------|
| Dialogue Parser | `adaptive_mind_2501/dialogue_parser/` | NL intent (rule-based + optional Ollama) |
| Knowledge Engine | `adaptive_mind_2501/knowledge_engine/` | Semantic graph memory (NetworkX + JSON) |
| Task Planner | `adaptive_mind_2501/task_planner/` | Macro-goals → robotic task sequences |
| Safety Governor | `adaptive_mind_2501/safety_governor/` | Three Laws / real-time action gate |
| Proactive Engine | `adaptive_mind_2501/proactive_engine/` | Context triggers (battery, idle, goals) |
| **Brain Node** | `adaptive_mind_2501/brain_node.py` | ROS 2 orchestrator / standalone mock |

```
/adaptive_mind/user_command
        │
        ▼
 DialogueParser ──► GraphMemory ──► TaskPlanner ──► SafetyGovernor
        ▲                                                 │
        │                                                 ▼
 ProactiveEngine (timer)                     /adaptive_mind/task_queue
                                             /adaptive_mind/status
```

**Topics**

| Topic | Type | Direction |
|-------|------|-----------|
| `/adaptive_mind/user_command` | `std_msgs/String` | NL input |
| `/adaptive_mind/battery` | `std_msgs/String` | battery level |
| `/adaptive_mind/task_queue` | `std_msgs/String` | approved tasks (JSON) |
| `/adaptive_mind/status` | `std_msgs/String` | status / heartbeat (JSON) |

---

## Dependencies

- **ROS 2 Humble** — `rclpy`, `std_msgs`, `launch`, `launch_ros`
- **Python 3** — `networkx`, `requests`
- Optional — [Ollama](https://ollama.com) for local LLM parsing

---

## Build & launch (ROS 2 Humble)

```bash
mkdir -p ~/ros2_ws/src
ln -s /path/to/Adaptive_Mind_2501 ~/ros2_ws/src/adaptive_mind_2501
cd ~/ros2_ws

source /opt/ros/humble/setup.bash
rosdep install --from-paths src -y --ignore-src
colcon build --packages-select adaptive_mind_2501
source install/setup.bash

ros2 launch adaptive_mind_2501 brain.launch.py
```

Send a natural-language command:

```bash
ros2 topic pub --once /adaptive_mind/user_command std_msgs/msg/String \
  "{data: 'vai in cucina e prendi il bicchiere'}"
```

Parameters live in [`config/brain_params.yaml`](config/brain_params.yaml).

---

## Standalone / Mock (no ROS)

If `rclpy` is missing, Project 2501 runs the full cognitive pipeline offline and
prints mock topic traffic to the terminal.

```bash
cd /path/to/Adaptive_Mind_2501
python3 -m venv .venv && source .venv/bin/activate
pip install networkx requests

PYTHONPATH=. python3 -m adaptive_mind_2501.brain_node \
  "vai in cucina e prendi il bicchiere"

# Interactive REPL
PYTHONPATH=. python3 -m adaptive_mind_2501.brain_node
# commands: <NL> | battery <%> | tick | status | quit
```

---

## Offline smoke test

```bash
cd /path/to/Adaptive_Mind_2501
PYTHONPATH=. python3 - <<'PY'
from adaptive_mind_2501.brain_node import AdaptiveMindBrain

brain = AdaptiveMindBrain(graph_path='/tmp/project2501_smoke.json')
out = brain.process_command('vai in cucina e prendi il bicchiere')
assert out[0]['action'] == 'go_to' and out[1]['action'] == 'pick_up'
stop = brain.process_command('stop')
assert stop[0]['action'] == 'emergency_stop'
print('SMOKE_OK', out)
PY
```

---

## Repository layout

```
Adaptive_Mind_2501/
├── adaptive_mind_2501/     # Python package (Project 2501 core)
├── config/                 # ROS parameters
├── launch/                 # brain.launch.py
├── resource/               # ament index marker
├── package.xml
├── setup.py / setup.cfg
├── LICENSE                 # Apache-2.0
└── README.md
```

---

## License

Copyright 2026 Project 2501  
Licensed under the [Apache License 2.0](LICENSE).
