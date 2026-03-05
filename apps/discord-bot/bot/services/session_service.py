from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any


logger = logging.getLogger("nexus.discord.session_service")


@dataclass
class SessionService:
    """
    إدارة جلسات اللاعبين النشطة.

    ملاحظات إنتاجية:
    - هذا الإصدار in-memory (سريع للتطوير).
    - في الإنتاج الحقيقي: انقله إلى DB (Redis/PostgreSQL/SQLite) مع فهارس.
    """

    # user_id -> session
    _sessions: dict[int, dict[str, Any]] = field(default_factory=dict)

    # guild_id -> set(user_id)  (لتسريع إحصائية الجلسات لكل سيرفر)
    _guild_index: dict[int, set[int]] = field(default_factory=dict)

    # ---------------------------------------------------------
    # API رئيسي
    # ---------------------------------------------------------

    async def get_current_session(self, user_id: int) -> Optional[dict]:
        session = self._sessions.get(int(user_id))
        if not session:
            return None
        return dict(session)

    async def set_current_session(self, user_id: int, *, world_id: str, part_id: str) -> None:
        uid = int(user_id)
        world_id = self._normalize_world_id(world_id)
        now = self._now()

        existing = self._sessions.get(uid)
        if existing:
            existing["world_id"] = world_id
            existing["part_id"] = str(part_id)
            existing["updated_at"] = now
            self._sessions[uid] = existing
            logger.debug("Session updated | user=%s world=%s part=%s", uid, world_id, part_id)
            return

        self._sessions[uid] = {
            "user_id": uid,
            "guild_id": None,           # يمكن تعبئته من set_session_context
            "channel_id": None,         # يمكن تعبئته من set_session_context
            "world_id": world_id,
            "part_id": str(part_id),
            "created_at": now,
            "updated_at": now,
        }
        logger.debug("Session created | user=%s world=%s part=%s", uid, world_id, part_id)

    async def set_session_context(
        self,
        user_id: int,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
    ) -> None:
        """
        يربط الجلسة الحالية بسياق Discord (guild/channel) لدعم الإحصائيات والاسترجاع.
        """
        uid = int(user_id)
        session = self._sessions.get(uid)
        if not session:
            # لا ننشئ جلسة ناقصة بدون world/part
            return

        old_gid = session.get("guild_id")
        new_gid = int(guild_id) if guild_id is not None else None

        session["guild_id"] = new_gid
        session["channel_id"] = int(channel_id) if channel_id is not None else None
        session["updated_at"] = self._now()
        self._sessions[uid] = session

        # index maintenance
        if old_gid is not None and old_gid in self._guild_index:
            self._guild_index[old_gid].discard(uid)
            if not self._guild_index[old_gid]:
                del self._guild_index[old_gid]

        if new_gid is not None:
            self._guild_index.setdefault(new_gid, set()).add(uid)

    async def clear_current_session(self, user_id: int) -> None:
        uid = int(user_id)
        session = self._sessions.pop(uid, None)
        if not session:
            return

        gid = session.get("guild_id")
        if gid is not None and gid in self._guild_index:
            self._guild_index[gid].discard(uid)
            if not self._guild_index[gid]:
                del self._guild_index[gid]

        logger.debug("Session cleared | user=%s", uid)

    # ---------------------------------------------------------
    # Recovery / Restore
    # ---------------------------------------------------------

    async def get_active_sessions(self) -> list[dict]:
        """
        تستخدم عند startup لاستعادة الـ persistent views.
        """
        out: list[dict] = []
        for s in self._sessions.values():
            world_id = s.get("world_id")
            part_id = s.get("part_id")
            user_id = s.get("user_id")
            if not user_id or not world_id or not part_id:
                continue
            out.append({
                "user_id": int(user_id),
                "world_id": str(world_id),
                "part_id": str(part_id),
            })
        return out

    async def get_active_sessions_count(self, guild_id: Optional[int] = None) -> int:
        if guild_id is None:
            return len(self._sessions)
        return len(self._guild_index.get(int(guild_id), set()))

    # ---------------------------------------------------------
    # Maintenance
    # ---------------------------------------------------------

    async def prune_stale_sessions(self, max_idle_minutes: int = 1440) -> int:
        """
        حذف الجلسات القديمة غير النشطة.
        افتراضي: 24 ساعة.
        """
        if max_idle_minutes <= 0:
            return 0

        now = datetime.now(timezone.utc)
        to_delete: list[int] = []

        for uid, s in self._sessions.items():
            updated = s.get("updated_at")
            if not isinstance(updated, str):
                continue
            try:
                dt = datetime.fromisoformat(updated)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = now - dt
                if delta.total_seconds() > max_idle_minutes * 60:
                    to_delete.append(uid)
            except Exception:
                # إذا timestamp فاسد، احذفه احتياطياً
                to_delete.append(uid)

        for uid in to_delete:
            await self.clear_current_session(uid)

        if to_delete:
            logger.info("Pruned %s stale sessions", len(to_delete))
        return len(to_delete)

    # ---------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------

    def _normalize_world_id(self, world_id: str) -> str:
        aliases = {"past": "retro", "alt": "alternate"}
        world_id = (world_id or "").strip().lower()
        return aliases.get(world_id, world_id)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
