from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.registrar import CommandRegistrar
from bot.services.story_service import StoryService
from bot.services.progression_service import ProgressionService
from bot.services.channel_policy_service import ChannelPolicyService
from bot.services.session_service import SessionService
from bot.services.metrics_service import MetricsService
from bot.services.player_service import PlayerService


# ============================================================
# Logging
# ============================================================

def setup_logging() -> None:
    level_name = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


logger = logging.getLogger("nexus.discord.main")


# ============================================================
# Settings
# ============================================================

@dataclass(frozen=True)
class AppSettings:
    token: str
    content_dir: str
    policy_mode: str
    command_prefix: str
    status_text: str
    startup_timeout_sec: int
    sync_global: bool
    debug_guild_id: Optional[int]

    @staticmethod
    def from_env() -> "AppSettings":
        load_dotenv()

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DISCORD_TOKEN غير موجود في البيئة.")

        content_dir = os.getenv("NEXUS_CONTENT_DIR", "./content").strip()
        policy_mode = os.getenv("CHANNEL_POLICY_MODE", "strict").strip().lower()
        if policy_mode not in {"strict", "soft", "off"}:
            policy_mode = "strict"

        command_prefix = os.getenv("BOT_PREFIX", "!").strip() or "!"
        status_text = os.getenv("BOT_STATUS", "🌍 النيكسس ينتظرك | /ابدأ").strip()

        timeout_raw = os.getenv("BOT_STARTUP_TIMEOUT_SEC", "40").strip()
        startup_timeout_sec = int(timeout_raw) if timeout_raw.isdigit() else 40

        sync_global_raw = os.getenv("SYNC_GLOBAL_COMMANDS", "true").strip().lower()
        sync_global = sync_global_raw in {"1", "true", "yes", "on"}

        guild_raw = os.getenv("BOT_GUILD_ID", "").strip()
        debug_guild_id = int(guild_raw) if guild_raw.isdigit() else None

        return AppSettings(
            token=token,
            content_dir=content_dir,
            policy_mode=policy_mode,
            command_prefix=command_prefix,
            status_text=status_text,
            startup_timeout_sec=startup_timeout_sec,
            sync_global=sync_global,
            debug_guild_id=debug_guild_id,
        )


# ============================================================
# Service Container
# ============================================================

@dataclass(frozen=True)
class ServiceContainer:
    story: StoryService
    progression: ProgressionService
    policy: ChannelPolicyService
    policy_admin: ChannelPolicyService
    sessions: SessionService
    players: PlayerService
    metrics: MetricsService
    registrar: CommandRegistrar


def build_services(settings: AppSettings) -> ServiceContainer:
    story = StoryService(
        content_root=__import__("pathlib").Path(settings.content_dir),
        strict_validation=True,
        allow_loops=False,  # مه لمنع حلقات القصة غير المقصودة
    )
    policy = ChannelPolicyService(default_mode=settings.policy_mode)
    sessions = SessionService()
    players = PlayerService()
    metrics = MetricsService()
    progression = ProgressionService(story=story)
    registrar = CommandRegistrar()

    return ServiceContainer(
        story=story,
        progression=progression,
        policy=policy,
        policy_admin=policy,   # نفس الخدمة للأوامر الإدارية + أوامر القصة
        sessions=sessions,
        players=players,
        metrics=metrics,
        registrar=registrar,
    )


# ============================================================
# Bot
# ============================================================

class NexusBot(commands.Bot):
    def __init__(self, settings: AppSettings, svc: ServiceContainer) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = False  # سلاش-أول، أأمن

        super().__init__(
            command_prefix=settings.command_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )

        self.settings = settings
        self.svc = svc
        self._ready_once = False

    async def setup_hook(self) -> None:
        """
        Startup sequence:
        1) Validate/load content
        2) Register commands extensions
        3) Restore persistent views from active sessions
        4) Sync slash commands
        """
        await self._guard_with_timeout("story.healthcheck", self.svc.story.healthcheck())
        await self._guard_with_timeout("story.load_runtime_bundles", self.svc.story.load_runtime_bundles())
        await self._guard_with_timeout("commands.register", self.svc.registrar.register(self))
        await self._guard_with_timeout("views.restore", self._restore_views())
        await self._guard_with_timeout("commands.sync", self._sync_commands())

    async def _sync_commands(self) -> None:
        if self.settings.debug_guild_id:
            guild = discord.Object(id=self.settings.debug_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("✅ تمت مزامنة %s أمر في Guild الاختبار %s", len(synced), self.settings.debug_guild_id)
            return

        if self.settings.sync_global:
            synced = await self.tree.sync()
            logger.info("✅ تمت مزامنة %s أمر (Global)", len(synced))
        else:
            logger.warning("⚠️ مزامنة الأوامر global معطلة من الإعدادات.")

    async def _restore_views(self) -> None:
        """
        استعادة الأزرار الثابتة بعد إعادة التشغيل.
        يعتمد على sessions.get_active_sessions()
        """
        restored = 0
        skipped = 0

        active = await self.svc.sessions.get_active_sessions()
        for row in active:
            user_id = row.get("user_id")
            world_id = row.get("world_id")
            part_id = row.get("part_id")

            if not user_id or not world_id or not part_id:
                skipped += 1
                continue

            part = await self.svc.story.get_part(world_id, part_id)
            if not part:
                skipped += 1
                continue

            # استيراد كسول لتجنب circular import
            from bot.commands.story import PersistentStoryView

            try:
                view = PersistentStoryView(
                    services=self.svc,
                    user_id=int(user_id),
                    world_id=str(world_id),
                    part=part,
                )
                self.add_view(view)  # persistent view registration
                restored += 1
            except Exception:
                logger.exception(
                    "❌ فشل استعادة view للجلسة user=%s world=%s part=%s",
                    user_id, world_id, part_id
                )
                skipped += 1

        logger.info("♻️ استعادة الأزرار: restored=%s skipped=%s", restored, skipped)
        await self.svc.metrics.gauge("bot.views.restored", float(restored))
        await self.svc.metrics.gauge("bot.views.skipped", float(skipped))

    async def on_ready(self) -> None:
        if self._ready_once:
            return
        self._ready_once = True

        logger.info("✅ Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")
        await self.change_presence(activity=discord.Game(name=self.settings.status_text))
        await self.svc.metrics.inc("bot.ready.count")

    async def _guard_with_timeout(self, label: str, coro) -> None:
        try:
            await asyncio.wait_for(coro, timeout=self.settings.startup_timeout_sec)
        except asyncio.TimeoutError as exc:
            logger.critical("⏱️ Timeout أثناء startup: %s", label)
            raise RuntimeError(f"Startup timeout in: {label}") from exc
        except Exception:
            logger.exception("💥 فشل startup step: %s", label)
            raise

    async def close(self) -> None:
        try:
            await self.svc.metrics.inc("bot.shutdown.count")
        finally:
            await super().close()


# ============================================================
# Entry
# ============================================================

async def run() -> int:
    setup_logging()

    try:
        settings = AppSettings.from_env()
    except Exception as exc:
        logger.critical("❌ خطأ إعدادات: %s", exc)
        return 1

    svc = build_services(settings)
    bot = NexusBot(settings=settings, svc=svc)

    try:
        await bot.start(settings.token)
        return 0
    except KeyboardInterrupt:
        logger.warning("🛑 إيقاف يدوي.")
        return 130
    except Exception:
        logger.exception("💥 خطأ قاتل أثناء تشغيل البوت.")
        return 1
    finally:
        await bot.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
