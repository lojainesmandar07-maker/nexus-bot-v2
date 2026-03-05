from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass
class RateLimitRule:
    key: str
    limit: int
    window_sec: int


@dataclass
class RateLimitService:
    """
    Sliding window-ish limiter بسيط.
    key المقترح:
      - cmd:<command_name>:<user_id>
      - btn:<user_id>:<part_id>
    """

    # key -> list[timestamps]
    _hits: dict[str, list[float]] = field(default_factory=dict)

    async def check(self, key: str, *, limit: int, window_sec: int) -> tuple[bool, float]:
        """
        Returns:
          allowed, retry_after_seconds
        """
        if limit <= 0 or window_sec <= 0:
            return True, 0.0

        now = _now_ts()
        arr = self._hits.setdefault(key, [])

        # prune old
        threshold = now - window_sec
        arr[:] = [t for t in arr if t > threshold]

        if len(arr) >= limit:
            oldest_in_window = min(arr) if arr else now
            retry_after = max(0.0, (oldest_in_window + window_sec) - now)
            return False, retry_after

        arr.append(now)
        return True, 0.0

    async def check_command(self, command_name: str, user_id: int, *, limit: int, window_sec: int) -> tuple[bool, float]:
        key = f"cmd:{command_name}:{int(user_id)}"
        return await self.check(key, limit=limit, window_sec=window_sec)

    async def check_button(self, user_id: int, part_id: str, *, limit: int, window_sec: int) -> tuple[bool, float]:
        key = f"btn:{int(user_id)}:{part_id}"
        return await self.check(key, limit=limit, window_sec=window_sec)

    async def clear_key(self, key: str) -> None:
        self._hits.pop(key, None)

    async def clear_all(self) -> None:
        self._hits.clear()
