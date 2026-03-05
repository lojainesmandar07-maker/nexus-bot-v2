from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Optional

import discord


logger = logging.getLogger("nexus.discord.recovery.view_rehydrator")


class SessionServicePort(Protocol):
    async def get_active_sessions(self) -> list[dict]:
        ...


class StoryServicePort(Protocol):
    async def get_part(self, world_id: str, part_id: str) -> Optional[dict]:
        ...


class MetricsPort(Protocol):
    async def inc(self, key: str, value: int = 1) -> None:
        ...

    async def gauge(self, key: str, value: float) -> None:
        ...


class ServicesPort(Protocol):
    sessions: SessionServicePort
    story: StoryServicePort
    metrics: MetricsPort


@dataclass
class ViewRehydrator:
    """
    Re-add persistent views at startup.
    """

    bot: discord.Client
    services: ServicesPort

    async def run(self) -> None:
        # lazy import to avoid circular imports
        from bot.ui.views.persistent_story_view import PersistentStoryView

        restored = 0
        skipped = 0

        sessions = await self.services.sessions.get_active_sessions()
        for state in sessions:
            user_id = state.get("user_id")
            world_id = state.get("world_id")
            part_id = state.get("part_id")

            if not user_id or not world_id or not part_id:
                skipped += 1
                continue

            part = await self.services.story.get_part(str(world_id), str(part_id))
            if not part:
                # Safety behavior: node missing; skip restoring this view.
                # User can recover with /استمر
                skipped += 1
                logger.warning(
                    "Skip rehydrate: missing node user=%s world=%s part=%s (recover via /استمر)",
                    user_id, world_id, part_id
                )
                continue

            try:
                view = PersistentStoryView(
                    services=self.services,
                    user_id=int(user_id),
                    world_id=str(world_id),
                    part=part,
                )
                self.bot.add_view(view)
                restored += 1
            except Exception:
                skipped += 1
                logger.exception(
                    "Failed rehydrate view user=%s world=%s part=%s",
                    user_id, world_id, part_id
                )

        try:
            await self.services.metrics.gauge("rehydration.restored", float(restored))
            await self.services.metrics.gauge("rehydration.skipped", float(skipped))
            await self.services.metrics.inc("rehydration.runs", 1)
        except Exception:
            logger.exception("Failed to push rehydration metrics")

        logger.info("View rehydration done: restored=%s skipped=%s", restored, skipped)
