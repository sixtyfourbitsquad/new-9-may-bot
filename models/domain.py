"""Strongly typed domain enums and dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AdminRole(StrEnum):
    OWNER = "owner"
    MODERATOR = "moderator"
    SUPPORT = "support"


class UserBroadcastStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"


class BroadcastStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BroadcastJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class ScheduledJobStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class BroadcastStats:
    """Aggregated counters for UI / Redis snapshot."""

    total_users: int = 0
    delivered: int = 0
    failed: int = 0
    blocked: int = 0
    remaining: int = 0
    speed_per_sec: float = 0.0


@dataclass(slots=True)
class MessagePayload:
    """Serializable outbound message definition for broadcast / scheduler / retention."""

    kind: str  # text, photo, video, audio, voice, document, sticker, poll, album, forward, copy
    data: dict[str, Any] = field(default_factory=dict)
    inline_keyboard: list[list[dict[str, Any]]] | None = None
    disable_notification: bool = False
