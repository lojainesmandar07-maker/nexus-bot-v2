from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("nexus.discord.metrics_service")


@dataclass
class MetricsService:
    """
    خدمة Metrics خفيفة (In-Memory) وجاهزة للتبديل لاحقًا.

    لاحقًا يمكنك استبدالها بسهولة بـ:
    - Prometheus
    - OpenTelemetry
    - Redis counters
    - أي analytics backend
    """

    _counters: dict[str, int] = field(default_factory=dict)
    _gauges: dict[str, float] = field(default_factory=dict)
    _events: list[dict[str, Any]] = field(default_factory=list)
    _max_events: int = 5000

    async def inc(self, key: str, value: int = 1) -> None:
        if not key:
            return
        current = self._counters.get(key, 0)
        self._counters[key] = current + int(value)

    async def gauge(self, key: str, value: float) -> None:
        if not key:
            return
        self._gauges[key] = float(value)

    async def event(self, name: str, payload: dict[str, Any] | None = None) -> None:
        if not name:
            return
        item = {
            "name": name,
            "payload": payload or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._events.append(item)
        if len(self._events) > self._max_events:
            del self._events[:-self._max_events]

    async def get_snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "events_count": len(self._events),
        }

    async def reset(self) -> None:
        self._counters.clear()
        self._gauges.clear()
        self._events.clear()
        logger.info("Metrics reset completed.")
