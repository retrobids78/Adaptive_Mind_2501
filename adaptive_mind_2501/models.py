"""Shared data models for Adaptive Mind 2501."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Intent:
    name: str
    action: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Task:
    task_id: str
    action: str
    target: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: str = 'pending'


@dataclass
class Action:
    command: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextEvent:
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
