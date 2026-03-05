from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


logger = logging.getLogger("nexus.discord.player_service")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_player(user_id: int, username: str) -> dict[str, Any]:
    return {
        "user_id": int(user_id),
        "username": username,
        "level": 1,
        "xp": 0,
        "shards": 0,
        "gold": 0,
        "corruption": 0,
        "current_world": "fantasy",
        "traits": {
            "brave": 0,
            "greedy": 0,
            "diplomatic": 0,
            "chaotic": 0,
        },
        "endings": {
            "fantasy": None,
            "retro": None,
            "future": None,
            "alternate": None,
        },
        "world_progress": {
            "fantasy": None,
            "retro": None,
            "future": None,
            "alternate": None,
        },
        "created_at": _now(),
        "updated_at": _now(),
    }


@dataclass
class PlayerService:
    """
    Player service نظيف داخل الذاكرة + history للقرارات.
    """

    _players: dict[int, dict[str, Any]] = field(default_factory=dict)
    _history: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    _history_limit_per_user: int = 3000

    # ---------------------------------------------------------
    # Player CRUD
    # ---------------------------------------------------------

    async def get_or_create_player(self, user_id: int, username: str) -> dict:
        uid = int(user_id)
        player = self._players.get(uid)
        if player:
            return copy.deepcopy(player)

        player = _default_player(uid, username)
        self._players[uid] = player
        logger.info("Created new player user_id=%s username=%s", uid, username)
        return copy.deepcopy(player)

    async def get_player(self, user_id: int) -> Optional[dict]:
        uid = int(user_id)
        p = self._players.get(uid)
        return copy.deepcopy(p) if p else None

    async def update_player(self, user_id: int, updates: dict) -> None:
        uid = int(user_id)
        existing = self._players.get(uid)

        if not existing:
            # لو اللاعب غير موجود، أنشئه بأقل قدر ممكن ثم دمج
            existing = _default_player(uid, updates.get("username", f"user_{uid}"))

        # merge shallow (مقصود في هذه المرحلة)
        merged = dict(existing)
        merged.update(updates)
        merged["updated_at"] = _now()

        # normalize mandatory structures
        merged.setdefault("traits", {})
        merged.setdefault("endings", {
            "fantasy": None, "retro": None, "future": None, "alternate": None
        })
        merged.setdefault("world_progress", {
            "fantasy": None, "retro": None, "future": None, "alternate": None
        })

        self._players[uid] = merged

    async def delete_player(self, user_id: int) -> None:
        uid = int(user_id)
        self._players.pop(uid, None)
        self._history.pop(uid, None)

    # ---------------------------------------------------------
    # Choice history
    # ---------------------------------------------------------

    async def save_choice_history(
        self,
        user_id: int,
        *,
        world_id: str,
        from_part_id: str,
        choice_id: str,
        choice_text: str,
        next_part_id: str,
        effects: dict,
    ) -> None:
        uid = int(user_id)
        self._history.setdefault(uid, [])

        self._history[uid].append({
            "world_id": world_id,
            "from_part_id": from_part_id,
            "choice_id": choice_id,
            "choice_text": choice_text,
            "next_part_id": next_part_id,
            "effects": effects or {},
            "timestamp": _now(),
        })

        # cap
        if len(self._history[uid]) > self._history_limit_per_user:
            overflow = len(self._history[uid]) - self._history_limit_per_user
            del self._history[uid][:overflow]

    async def get_choice_history(self, user_id: int, limit: int = 20) -> list[dict]:
        uid = int(user_id)
        rows = self._history.get(uid, [])
        if limit <= 0:
            return []
        return copy.deepcopy(rows[-limit:][::-1])

    async def clear_choice_history(self, user_id: int) -> None:
        uid = int(user_id)
        self._history[uid] = []

    # ---------------------------------------------------------
    # Stats helpers
    # ---------------------------------------------------------

    async def get_users_count(self) -> int:
        return len(self._players)

    async def get_player_summary(self, user_id: int) -> Optional[dict]:
        p = await self.get_player(user_id)
        if not p:
            return None

        h_count = len(self._history.get(int(user_id), []))
        return {
            "user_id": p.get("user_id"),
            "username": p.get("username"),
            "level": p.get("level"),
            "xp": p.get("xp"),
            "current_world": p.get("current_world"),
            "history_count": h_count,
            "updated_at": p.get("updated_at"),
        }
