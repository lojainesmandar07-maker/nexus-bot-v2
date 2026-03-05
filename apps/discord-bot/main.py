from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from bot.app import (
    AppServices,
    BotSettings,
    create_bot,
)

# ============================================================
# Basic logging
# ============================================================

def setup_logging() -> None:
    level_name = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


# ============================================================
# Minimal concrete services (production-safe stubs)
# Replace internals with your real packages implementations.
# ============================================================

class StoryServiceImpl:
    """
    Wire to packages/content-engine in your mono-repo:
      - healthcheck()
      - load_runtime_bundles()
      - get_start_part_id(world_id)
    """

    def __init__(self, content_dir: str) -> None:
        self.content_dir = content_dir
        self._loaded = False

    async def healthcheck(self) -> None:
        if not os.path.isdir(self.content_dir):
            raise RuntimeError(f"Content directory not found: {self.content_dir}")

    async def load_runtime_bundles(self) -> None:
        # TODO: replace with content-engine loader+validator+compiler calls
        # For now only marks loaded if dir exists.
        if not os.path.isdir(self.content_dir):
            raise RuntimeError(f"Cannot load bundles; missing content dir: {self.content_dir}")
        self._loaded = True

    async def get_start_part_id(self, world_id: str) -> str:
        # TODO: resolve from world manifest/compiled bundle
        mapping = {
            "fantasy": "FANTASY_001",
            "retro": "RETRO_001",
            "future": "FUTURE_001",
            "alternate": "ALT_001",
        }
        return mapping.get(world_id, "FANTASY_001")


class PlayerServiceImpl:
    """Replace with packages/infra-db repository implementation."""

    def __init__(self) -> None:
        self.players: dict[int, dict] = {}

    async def get_or_create_player(self, user_id: int, username: str) -> dict:
        player = self.players.get(user_id)
        if player:
            return player

        player = {
            "user_id": user_id,
            "username": username,
            "level": 1,
            "xp": 0,
            "shards": 0,
            "corruption": 0,
            "current_world": "fantasy",
            "world_progress": {
                "fantasy": None,
                "retro": None,
                "future": None,
                "alternate": None,
            },
            "endings": {
                "fantasy": None,
                "retro": None,
                "future": None,
                "alternate": None,
            },
            "traits": {
                "brave": 0,
                "greedy": 0,
                "diplomatic": 0,
                "chaotic": 0,
            },
        }
        self.players[user_id] = player
        return player

    async def get_current_part(self, user_id: int, world_id: str) -> Optional[str]:
        player = self.players.get(user_id)
        if not player:
            return None
        return player.get("world_progress", {}).get(world_id)


class ChannelPolicyServiceImpl:
    """
    strict: block outside mapped channel
    soft: allow + warning
    off: always allow
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._mapping: dict[int, dict[str, int]] = {}  # guild_id -> world_id -> channel_id

    async def validate_usage(
        self,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
        world_id: str,
    ) -> tuple[bool, Optional[str]]:
        if guild_id is None:
            return True, None  # DM allowed

        if self.mode == "off":
            return True, None

        world_map = self._mapping.get(guild_id, {})
        target_channel = world_map.get(world_id)

        if target_channel is None:
            # no mapping configured
            if self.mode == "strict":
                return False, f"World channel for `{world_id}` is not configured by admin."
            return True, None

        if channel_id == target_channel:
            return True, None

        if self.mode == "soft":
            return True, f"⚠️ Recommended channel for `{world_id}` is <#{target_channel}>."

        return False, f"❌ Use <#{target_channel}> for `{world_id}`."


class SessionServiceImpl:
    """Replace with DB-backed active-session query used for persistent view restoration."""

    async def get_active_sessions(self) -> list[dict]:
        # Expected format:
        # [{"user_id": 123, "world_id": "fantasy", "part_id": "FANTASY_001"}]
        return []


class CommandRegistrarImpl:
    """
    Production command registration entrypoint.
    Keep this tiny: it imports your cogs package and mounts cogs.
    """

    async def register(self, bot) -> None:
        # Example:
        # from bot.commands.story import StoryCog
        # from bot.commands.worlds import WorldsCog
        # await bot.add_cog(StoryCog(bot))
        # await bot.add_cog(WorldsCog(bot))
        #
        # For now this is explicit stub to keep bootstrap valid.
        return


class PersistentViewFactoryImpl:
    """
    Must return persistent views (timeout=None) with stable custom_id.
    Replace with actual StoryView creation logic.
    """

    def create_story_view(self, *, user_id: int, world_id: str, part_id: str):
        import discord

        class EmptyPersistentView(discord.ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=None)

        return EmptyPersistentView()


class MetricsServiceImpl:
    """Replace with real analytics emitter (Redis/Prometheus/OpenTelemetry/etc.)."""

    async def inc(self, key: str, value: int = 1) -> None:
        logging.getLogger("nexus.metrics").debug("inc %s +%s", key, value)

    async def gauge(self, key: str, value: float) -> None:
        logging.getLogger("nexus.metrics").debug("gauge %s=%s", key, value)


# ============================================================
# Config
# ============================================================

@dataclass(frozen=True)
class RuntimeConfig:
    token: str
    content_dir: str
    settings: BotSettings


def load_runtime_config() -> RuntimeConfig:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing")

    content_dir = os.getenv("NEXUS_CONTENT_DIR", "./content").strip()
    app_name = os.getenv("BOT_NAME", "Nexus Bot V2").strip()
    command_prefix = os.getenv("BOT_PREFIX", "!").strip() or "!"
    status_text = os.getenv("BOT_STATUS", "🌍 Nexus awaits | /start").strip()
    sync_global_raw = os.getenv("SYNC_GLOBAL_COMMANDS", "true").strip().lower()
    sync_global = sync_global_raw in {"1", "true", "yes", "on"}

    debug_guild = os.getenv("BOT_GUILD_ID", "").strip()
    debug_guild_id = int(debug_guild) if debug_guild.isdigit() else None

    startup_timeout_raw = os.getenv("BOT_STARTUP_TIMEOUT_SEC", "30").strip()
    startup_timeout = int(startup_timeout_raw) if startup_timeout_raw.isdigit() else 30

    settings = BotSettings(
        app_name=app_name,
        command_prefix=command_prefix,
        status_text=status_text,
        sync_global=sync_global,
        debug_guild_id=debug_guild_id,
        startup_timeout_sec=startup_timeout,
    )

    return RuntimeConfig(
        token=token,
        content_dir=content_dir,
        settings=settings,
    )


def build_services(cfg: RuntimeConfig) -> AppServices:
    policy_mode = os.getenv("CHANNEL_POLICY_MODE", "strict").strip().lower()

    return AppServices(
        story=StoryServiceImpl(content_dir=cfg.content_dir),
        players=PlayerServiceImpl(),
        policy=ChannelPolicyServiceImpl(mode=policy_mode),
        sessions=SessionServiceImpl(),
        registrar=CommandRegistrarImpl(),
        views=PersistentViewFactoryImpl(),
        metrics=MetricsServiceImpl(),
    )


# ============================================================
# Process entrypoint with reliable exit codes
# ============================================================

async def run() -> int:
    setup_logging()
    log = logging.getLogger("nexus.main")

    try:
        cfg = load_runtime_config()
    except Exception as exc:
        log.critical("Configuration error: %s", exc)
        return 1

    services = build_services(cfg)
    bot = create_bot(settings=cfg.settings, services=services)

    try:
        await bot.start(cfg.token)
        return 0
    except KeyboardInterrupt:
        log.warning("Shutdown requested by keyboard interrupt.")
        return 130
    except Exception:
        log.exception("Fatal runtime error.")
        return 1
    finally:
        await bot.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
