from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass
class InteractionGuardService:
    """
    يمنع:
    - double-click السريع على نفس التفاعل
    - إعادة معالجة نفس custom_id/interaction key (idempotency)
    """

    ttl_sec: int = 20

    # interaction_key -> expire_ts
    _locks: dict[str, float] = field(default_factory=dict)
    _idem_keys: dict[str, float] = field(default_factory=dict)

    def _cleanup(self) -> None:
        now = _now_ts()
        self._locks = {k: v for k, v in self._locks.items() if v > now}
        self._idem_keys = {k: v for k, v in self._idem_keys.items() if v > now}

    async def acquire_lock(self, interaction_key: str) -> bool:
        """
        Return True if lock acquired, False if already locked.
        """
        self._cleanup()
        now = _now_ts()
        exp = self._locks.get(interaction_key)
        if exp and exp > now:
            return False
        self._locks[interaction_key] = now + self.ttl_sec
        return True

    async def release_lock(self, interaction_key: str) -> None:
        self._locks.pop(interaction_key, None)

    async def seen_idempotency_key(self, idem_key: str) -> bool:
        """
        True if already processed recently.
        """
        self._cleanup()
        now = _now_ts()
        exp = self._idem_keys.get(idem_key)
        return bool(exp and exp > now)

    async def mark_idempotency_key(self, idem_key: str, ttl_sec: int | None = None) -> None:
        self._cleanup()
        now = _now_ts()
        ttl = ttl_sec if ttl_sec is not None else self.ttl_sec
        self._idem_keys[idem_key] = now + max(1, int(ttl))

    async def run_guarded(
        self,
        *,
        interaction_key: str,
        idempotency_key: str,
    ) -> tuple[bool, str]:
        """
        helper:
          returns (allowed, reason)
        """
        if await self.seen_idempotency_key(idempotency_key):
            return False, "duplicate"

        acquired = await self.acquire_lock(interaction_key)
        if not acquired:
            return False, "locked"

        # caller should call mark_idempotency_key() after success
        return True, "ok"
