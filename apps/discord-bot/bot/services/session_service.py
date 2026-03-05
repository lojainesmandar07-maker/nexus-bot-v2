from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    return aliases.get((world_id or "").strip().lower(), (world_id or "").strip().lower())


@dataclass
class SessionService:
    """
    Active session store (in-memory).
    لاحقًا استبدله بـ Redis/PostgreSQL بدون كسر الواجهة.
    """

    # user_id -> session
    _sessions: dict[int, dict[str, Any]] = field(default_factory=dict)

    # guild_id -> set(user_id) for quick admin stats
    _guild_index: dict[int, set[int]] = field(default_factory=dict)

    # -------- Core --------
    async def set_current_session(self, user_id: int, *, world_id: str, part_id: str) -> None:
        uid = int(user_id)
        world_id = _normalize_world_id(world_id)
        now = _now_iso()

        existing = self._sessions.get(uid)
        if existing:
            existing["world_id"] = world_id
            existing["part_id"] = str(part_id)
            existing["updated_at"] = now
            self._sessions[uid] = existing
            return

        self._sessions[uid] = {
            "user_id": uid,
            "world_id": world_id,
            "part_id": str(part_id),
            "guild_id": None,
            "channel_id": None,
            "created_at": now,
            "updated_at": now,
        }

    async def get_current_session(self, user_id: int) -> Optional[dict]:
        row = self._sessions.get(int(user_id))
        return dict(row) if row else None

    async def clear_current_session(self, user_id: int) -> None:
        uid = int(user_id)
        row = self._sessions.pop(uid, None)
        if not row:
            return

        gid = row.get("guild_id")
        if gid is not None and gid in self._guild_index:
            self._guild_index[gid].discard(uid)
            if not self._guild_index[gid]:
                del self._guild_index[gid]

    # -------- Context (guild/channel) --------
    async def set_session_context(
        self,
        user_id: int,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
    ) -> None:
        uid = int(user_id)
        row = self._sessions.get(uid)
        if not row:
            return  # avoid partial sessions

        old_gid = row.get("guild_id")
        new_gid = int(guild_id) if guild_id is not None else None

        row["guild_id"] = new_gid
        row["channel_id"] = int(channel_id) if channel_id is not None else None
        row["updated_at"] = _now_iso()
        self._sessions[uid] = row

        if old_gid is not None and old_gid in self._guild_index:
            self._guild_index[old_gid].discard(uid)
            if not self._guild_index[old_gid]:
                del self._guild_index[old_gid]

        if new_gid is not None:
            self._guild_index.setdefault(new_gid, set()).add(uid)

    # -------- Recovery hooks --------
    async def get_active_sessions(self) -> list[dict]:
        """
        For rehydration on startup:
        returns list of {user_id, world_id, part_id}
        """
        out: list[dict] = []
        for row in self._sessions.values():
            uid = row.get("user_id")
            wid = row.get("world_id")
            pid = row.get("part_id")
            if uid and wid and pid:
                out.append({
                    "user_id": int(uid),
                    "world_id": str(wid),
                    "part_id": str(pid),
                })
        return out

    async def get_active_sessions_count(self, guild_id: Optional[int] = None) -> int:
        if guild_id is None:
            return len(self._sessions)
        return len(self._guild_index.get(int(guild_id), set()))

    # -------- Safety maintenance --------
    async def prune_stale_sessions(self, max_idle_minutes: int = 1440) -> int:
        if max_idle_minutes <= 0:
            return 0

        now = datetime.now(timezone.utc)
        remove: list[int] = []

        for uid, row in self._sessions.items():
            ts = row.get("updated_at")
            if not isinstance(ts, str):
                remove.append(uid)
                continue
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).total_seconds() > max_idle_minutes * 60:
                    remove.append(uid)
            except Exception:
                remove.append(uid)

        for uid in remove:
            await self.clear_current_session(uid)

        return len(remove)
