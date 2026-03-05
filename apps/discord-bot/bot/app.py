from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol, Optional, Iterable

import discord
from discord.ext import commands

logger = logging.getLogger("nexus.discord.app")


# ============================================================
# Ports / Interfaces (so app.py stays production-clean)
# ============================================================

class StoryService(Protocol):
    async def healthcheck(self) -> None:
        ...

    async def load_runtime_bundles(self) -> None:
        ...

    async def get_start_part_id(self, world_id: str) -> str:
        ...


class PlayerService(Protocol):
    async def get_or_create_player(self, user_id: int, username: str) -> dict:
        ...

    async def get_current_part(self, user_id: int, world_id: str) -> Optional[str]:
        ...


class ChannelPolicyService(Protocol):
    async def validate_usage(
        self,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
        world_id: str,
    ) -> tuple[bool, Optional[str]]:
        ...


class SessionService(Protocol):
    async def get_active_sessions(self) -> list[dict]:
        """
        Must return:
        [
          {"user_id": int, "world_id": str, "part_id": str}
        ]
        """
        ...


class CommandRegistrar(Protocol):
    async def register(self, bot: commands.Bot) -> None:
        """
        Register cogs/slash commands.
        """
        ...


class PersistentViewFactory(Protocol):
    def create_story_view(self, *, user_id: int, world_id: str, part_id: str) -> discord.ui.View:
        """
        Must build a persistent view with timeout=None and deterministic custom_id handling.
        """
        ...


class MetricsService(Protocol):
    async def inc(self, key: str, value: int = 1) -> None:
        ...

    async def gauge(self, key: str, value: float) -> None:
        ...


# ============================================================
# Settings
# ============================================================

@dataclass(frozen=True)
class BotSettings:
    app_name: str
    command_prefix: str
    status_text: str
    sync_global: bool
    debug_guild_id: Optional[int]
    startup_timeout_sec: int = 30


# ============================================================
# Dependency Container
# ============================================================

@dataclass(frozen=True)
class AppServices:
    story: StoryService
    players: PlayerService
    policy: ChannelPolicyService
    sessions: SessionService
    registrar: CommandRegistrar
    views: PersistentViewFactory
    metrics: MetricsService


# ============================================================
# Bot Implementation
# ============================================================

class NexusDiscordBot(commands.Bot):
    """
    Production runtime bot:
    - no hardcoded story data
    - loads all content from content-engine services
    - restores persistent views ("button don't die")
    - sync strategy: debug guild or global
    """

    def __init__(self, settings: BotSettings, services: AppServices) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = False  # slash-first, safer

        super().__init__(
            command_prefix=settings.command_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )
        self.settings = settings
        self.svc = services
        self._ready_once = asyncio.Event()

    # ---------------------------
    # Lifecycle
    # ---------------------------

    async def setup_hook(self) -> None:
        logger.info("Bootstrapping %s ...", self.settings.app_name)

        # 1) Hard dependency health checks
        await self._guard_with_timeout("story.healthcheck", self.svc.story.healthcheck())
        await self._guard_with_timeout("story.load_runtime_bundles", self.svc.story.load_runtime_bundles())

        # 2) Register commands/cogs
        await self._guard_with_timeout("commands.register", self.svc.registrar.register(self))

        # 3) Restore persistent views from active sessions
        await self._guard_with_timeout("views.restore", self._restore_persistent_views())

        # 4) Sync slash commands
        await self._guard_with_timeout("commands.sync", self._sync_commands())

        logger.info("setup_hook completed successfully.")

    async def on_ready(self) -> None:
        if self._ready_once.is_set():
            return

        logger.info("✅ Logged in as %s (%s)", self.user, getattr(self.user, "id", "unknown"))
        await self.change_presence(activity=discord.Game(name=self.settings.status_text))
        await self.svc.metrics.inc("bot.ready.count", 1)
        self._ready_once.set()

    async def close(self) -> None:
        logger.info("Shutting down %s ...", self.settings.app_name)
        try:
            await self.svc.metrics.inc("bot.shutdown.count", 1)
        finally:
            await super().close()

    # ---------------------------
    # Internal startup tasks
    # ---------------------------

    async def _sync_commands(self) -> None:
        if self.settings.debug_guild_id:
            guild = discord.Object(id=self.settings.debug_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %d commands to debug guild %s", len(synced), self.settings.debug_guild_id)
        elif self.settings.sync_global:
            synced = await self.tree.sync()
            logger.info("Synced %d global commands", len(synced))
        else:
            logger.warning("Command sync disabled by settings.")

    async def _restore_persistent_views(self) -> None:
        restored = 0
        skipped = 0

        sessions = await self.svc.sessions.get_active_sessions()
        for state in sessions:
            user_id = state.get("user_id")
            world_id = state.get("world_id")
            part_id = state.get("part_id")

            if not user_id or not world_id or not part_id:
                skipped += 1
                continue

            try:
                view = self.svc.views.create_story_view(
                    user_id=int(user_id),
                    world_id=str(world_id),
                    part_id=str(part_id),
                )
                # key for observability only
                self.add_view(view, message_id=None)
                restored += 1
            except Exception:
                skipped += 1
                logger.exception(
                    "Failed to restore view for user=%s world=%s part=%s",
                    user_id, world_id, part_id
                )

        logger.info("Persistent views restored=%d skipped=%d", restored, skipped)
        await self.svc.metrics.gauge("bot.views.restored", float(restored))
        await self.svc.metrics.gauge("bot.views.restore_skipped", float(skipped))

    async def _guard_with_timeout(self, label: str, coro) -> None:
        try:
            await asyncio.wait_for(coro, timeout=self.settings.startup_timeout_sec)
        except asyncio.TimeoutError as exc:
            logger.critical("Startup task timeout: %s", label)
            raise RuntimeError(f"Startup timeout at {label}") from exc
        except Exception:
            logger.exception("Startup task failed: %s", label)
            raise

    # ---------------------------
    # Optional centralized checks (used by commands)
    # ---------------------------

    async def enforce_channel_policy(
        self,
        *,
        interaction: discord.Interaction,
        world_id: str,
    ) -> bool:
        """
        Call this from /start and /continue handlers.
        Returns True if allowed; otherwise sends ephemeral reason and returns False.
        """
        allowed, reason = await self.svc.policy.validate_usage(
            guild_id=interaction.guild.id if interaction.guild else None,
            channel_id=interaction.channel_id,
            world_id=world_id,
        )
        if allowed:
            return True

        if interaction.response.is_done():
            await interaction.followup.send(reason or "Channel policy blocked this action.", ephemeral=True)
        else:
            await interaction.response.send_message(
                reason or "Channel policy blocked this action.",
                ephemeral=True
            )
        return False


# ============================================================
# Factory Function
# ============================================================

def create_bot(*, settings: BotSettings, services: AppServices) -> NexusDiscordBot:
    """
    Single constructor for DI wiring from main.py.
    """
    return NexusDiscordBot(settings=settings, services=services)
