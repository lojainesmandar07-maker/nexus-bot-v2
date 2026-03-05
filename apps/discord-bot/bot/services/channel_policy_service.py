from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


logger = logging.getLogger("nexus.discord.channel_policy_service")


VALID_MODES = {"strict", "soft", "off"}


@dataclass
class ChannelPolicyService:
    """
    خدمة إدارة سياسة القنوات للعوالم.

    الأوضاع:
    - strict: يمنع التنفيذ خارج قناة العالم.
    - soft: يسمح مع تنبيه.
    - off: يسمح دائمًا بدون قيود.
    """

    default_mode: str = "strict"

    # تخزين مؤقت داخل الذاكرة (استبدله لاحقاً بمخزن DB)
    _guild_modes: dict[int, str] = field(default_factory=dict)
    _guild_world_channels: dict[int, dict[str, int]] = field(default_factory=dict)
    _audit: list[dict] = field(default_factory=list)

    # ---------------------------------------------------------
    # API المستخدمة من أوامر الإدارة
    # ---------------------------------------------------------

    async def set_world_channel(self, guild_id: int, world_id: str, channel_id: int, set_by: int) -> None:
        world_id = self._normalize_world_id(world_id)
        self._guild_world_channels.setdefault(guild_id, {})[world_id] = int(channel_id)
        self._audit_event(
            action="set_world_channel",
            guild_id=guild_id,
            world_id=world_id,
            channel_id=channel_id,
            by=set_by,
        )
        logger.info("Set world channel | guild=%s world=%s channel=%s by=%s", guild_id, world_id, channel_id, set_by)

    async def get_world_channel(self, guild_id: int, world_id: str) -> Optional[int]:
        world_id = self._normalize_world_id(world_id)
        return self._guild_world_channels.get(guild_id, {}).get(world_id)

    async def get_guild_world_channels(self, guild_id: int) -> dict[str, int]:
        return dict(self._guild_world_channels.get(guild_id, {}))

    async def set_policy_mode(self, guild_id: int, mode: str, set_by: int) -> None:
        mode = (mode or "").strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid policy mode: {mode}")

        self._guild_modes[guild_id] = mode
        self._audit_event(
            action="set_policy_mode",
            guild_id=guild_id,
            mode=mode,
            by=set_by,
        )
        logger.info("Set policy mode | guild=%s mode=%s by=%s", guild_id, mode, set_by)

    async def get_policy_mode(self, guild_id: int) -> str:
        mode = self._guild_modes.get(guild_id, self.default_mode)
        if mode not in VALID_MODES:
            return self.default_mode
        return mode

    # ---------------------------------------------------------
    # API المستخدمة من أوامر القصة
    # ---------------------------------------------------------

    async def validate_usage(
        self,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
        world_id: str,
    ) -> tuple[bool, Optional[str]]:
        """
        يعيد:
          (allowed, reason_message)

        reason_message:
          - في strict: رسالة منع
          - في soft: رسالة تنبيه
          - في off: None
        """
        world_id = self._normalize_world_id(world_id)

        # DM أو خارج سيرفر => سماح
        if guild_id is None:
            return True, None

        mode = await self.get_policy_mode(guild_id)
        if mode == "off":
            return True, None

        mapped_channel_id = await self.get_world_channel(guild_id, world_id)

        # لم يتم التعيين
        if mapped_channel_id is None:
            if mode == "strict":
                return (
                    False,
                    f"❌ لم يتم تعيين قناة لـ **{self._world_label(world_id)}** بعد. "
                    f"اطلب من الإدارة استخدام `/تعيين_عالم`."
                )
            # soft
            return (
                True,
                f"⚠️ لا توجد قناة معيّنة لـ **{self._world_label(world_id)}** حاليًا."
            )

        # القناة صحيحة
        if channel_id == mapped_channel_id:
            return True, None

        # القناة مختلفة
        if mode == "soft":
            return (
                True,
                f"⚠️ القناة الموصى بها لـ **{self._world_label(world_id)}** هي <#{mapped_channel_id}>."
            )

        # strict
        return (
            False,
            f"❌ استخدم قناة **{self._world_label(world_id)}** الصحيحة: <#{mapped_channel_id}>."
        )

    # ---------------------------------------------------------
    # أدوات تشخيص (للإدارة/اللوغ)
    # ---------------------------------------------------------

    async def get_policy_diagnostics(self, guild_id: int) -> dict:
        mode = await self.get_policy_mode(guild_id)
        mapping = await self.get_guild_world_channels(guild_id)
        return {
            "guild_id": guild_id,
            "mode": mode,
            "assigned_worlds": len(mapping),
            "mapping": mapping,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_audit_log(self, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        return self._audit[-limit:]

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _normalize_world_id(self, world_id: str) -> str:
        aliases = {"past": "retro", "alt": "alternate"}
        world_id = (world_id or "").strip().lower()
        return aliases.get(world_id, world_id)

    def _world_label(self, world_id: str) -> str:
        names = {
            "fantasy": "عالم الفانتازيا",
            "retro": "عالم الماضي",
            "future": "عالم المستقبل",
            "alternate": "الواقع البديل",
        }
        return names.get(world_id, world_id)

    def _audit_event(self, **payload) -> None:
        self._audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload,
        })
        # cap audit size
        if len(self._audit) > 5000:
            del self._audit[:-5000]
