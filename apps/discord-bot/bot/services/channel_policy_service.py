from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


VALID_POLICY_MODES = {"strict", "soft", "off"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    w = (world_id or "").strip().lower()
    return aliases.get(w, w)


@dataclass
class ChannelPolicyService:
    """
    Policy:
    - strict: يمنع خارج القناة المخصصة
    - soft: يسمح + تنبيه
    - off: يسمح دائمًا
    """

    default_mode: str = "strict"

    # guild_id -> mode
    _guild_modes: dict[int, str] = field(default_factory=dict)
    # guild_id -> world_id -> channel_id
    _guild_world_channels: dict[int, dict[str, int]] = field(default_factory=dict)
    _audit_log: list[dict] = field(default_factory=list)

    async def set_world_channel(self, guild_id: int, world_id: str, channel_id: int, set_by: int) -> None:
        world_id = _normalize_world_id(world_id)
        self._guild_world_channels.setdefault(int(guild_id), {})[world_id] = int(channel_id)
        self._audit_log.append({
            "ts": _now_iso(),
            "action": "set_world_channel",
            "guild_id": int(guild_id),
            "world_id": world_id,
            "channel_id": int(channel_id),
            "set_by": int(set_by),
        })

    async def get_world_channel(self, guild_id: int, world_id: str) -> Optional[int]:
        world_id = _normalize_world_id(world_id)
        return self._guild_world_channels.get(int(guild_id), {}).get(world_id)

    async def get_guild_world_channels(self, guild_id: int) -> dict[str, int]:
        return dict(self._guild_world_channels.get(int(guild_id), {}))

    async def set_policy_mode(self, guild_id: int, mode: str, set_by: int) -> None:
        mode = (mode or "").strip().lower()
        if mode not in VALID_POLICY_MODES:
            raise ValueError(f"invalid mode: {mode}")
        self._guild_modes[int(guild_id)] = mode
        self._audit_log.append({
            "ts": _now_iso(),
            "action": "set_policy_mode",
            "guild_id": int(guild_id),
            "mode": mode,
            "set_by": int(set_by),
        })

    async def get_policy_mode(self, guild_id: int) -> str:
        mode = self._guild_modes.get(int(guild_id), self.default_mode)
        if mode not in VALID_POLICY_MODES:
            return self.default_mode
        return mode

    async def validate_usage(
        self,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
        world_id: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Returns:
          (allowed, message)
        """
        world_id = _normalize_world_id(world_id)

        # DM allowed
        if guild_id is None:
            return True, None

        mode = await self.get_policy_mode(int(guild_id))
        if mode == "off":
            return True, None

        configured_channel = await self.get_world_channel(int(guild_id), world_id)

        if configured_channel is None:
            if mode == "strict":
                return False, f"❌ لم يتم تعيين قناة لـ `{world_id}` بعد. استخدم `/تعيين_عالم`."
            return True, f"⚠️ لا توجد قناة معيّنة لـ `{world_id}` حاليًا."

        if channel_id == configured_channel:
            return True, None

        if mode == "soft":
            return True, f"⚠️ القناة الموصى بها لـ `{world_id}` هي <#{configured_channel}>."
        return False, f"❌ استخدم القناة الصحيحة لـ `{world_id}`: <#{configured_channel}>."

    async def get_audit_log(self, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        return self._audit_log[-limit:]
