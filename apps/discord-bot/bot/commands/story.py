from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Protocol

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui.embeds.story_embed import build_story_embed
from bot.ui.embeds.status_embed import build_status_embed
from bot.ui.views.persistent_story_view import PersistentStoryView


logger = logging.getLogger("nexus.discord.commands.story")


# ============================================================
# Ports / Protocols
# ============================================================

class StoryServicePort(Protocol):
    async def get_start_part_id(self, world_id: str) -> str:
        ...

    async def get_part(self, world_id: str, part_id: str) -> Optional[dict]:
        ...

    async def get_ending(self, world_id: str, ending_id: str) -> Optional[dict]:
        ...


class PlayerServicePort(Protocol):
    async def get_or_create_player(self, user_id: int, username: str) -> dict:
        ...

    async def update_player(self, user_id: int, updates: dict) -> None:
        ...


class ProgressionServicePort(Protocol):
    async def can_access_world(self, player: dict, world_id: str) -> tuple[bool, str]:
        ...


class SessionServicePort(Protocol):
    async def get_current_session(self, user_id: int) -> Optional[dict]:
        ...

    async def set_current_session(self, user_id: int, *, world_id: str, part_id: str) -> None:
        ...

    async def set_session_context(
        self,
        user_id: int,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
    ) -> None:
        ...


class ChannelPolicyPort(Protocol):
    async def validate_usage(
        self,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
        world_id: str,
    ) -> tuple[bool, Optional[str]]:
        ...


class MetricsPort(Protocol):
    async def inc(self, key: str, value: int = 1) -> None:
        ...


class ServicesPort(Protocol):
    story: StoryServicePort
    players: PlayerServicePort
    progression: ProgressionServicePort
    sessions: SessionServicePort
    policy: ChannelPolicyPort
    metrics: MetricsPort


# ============================================================
# Constants
# ============================================================

WORLD_ORDER = ["fantasy", "retro", "future", "alternate"]

WORLD_NAMES_AR = {
    "fantasy": "عالم الفانتازيا",
    "retro": "عالم الماضي",
    "future": "عالم المستقبل",
    "alternate": "الواقع البديل",
}

WORLD_EMOJIS = {
    "fantasy": "🌲",
    "retro": "📜",
    "future": "🤖",
    "alternate": "🌀",
}

WORLD_COLORS = {
    "general": 0x5865F2,
    "error": 0xE74C3C,
}


def _normalize_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    world_id = (world_id or "").strip().lower()
    return aliases.get(world_id, world_id)


async def _metric(services: ServicesPort, key: str, value: int = 1) -> None:
    try:
        await services.metrics.inc(key, value)
    except Exception:
        logger.exception("Metric failed: %s", key)


# ============================================================
# Story Commands
# ============================================================

class StoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot, services: ServicesPort) -> None:
        self.bot = bot
        self.services = services

    @app_commands.command(name="ابدأ", description="🚀 ابدأ رحلتك في عالم النيكسس")
    @app_commands.describe(العالم="اختياري: اختر العالم الذي تريد البدء فيه")
    @app_commands.choices(العالم=[
        app_commands.Choice(name="🌲 عالم الفانتازيا", value="fantasy"),
        app_commands.Choice(name="📜 عالم الماضي", value="retro"),
        app_commands.Choice(name="🤖 عالم المستقبل", value="future"),
        app_commands.Choice(name="🌀 الواقع البديل", value="alternate"),
    ])
    async def start_command(
        self,
        interaction: discord.Interaction,
        العالم: Optional[str] = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            world_id = _normalize_world_id(العالم or "fantasy")
            user_id = interaction.user.id

            player = await self.services.players.get_or_create_player(user_id, interaction.user.name)

            # unlock
            allowed_world, world_reason = await self.services.progression.can_access_world(player, world_id)
            if not allowed_world:
                await interaction.followup.send(f"🔒 {world_reason}", ephemeral=True)
                await _metric(self.services, "story.start.locked")
                return

            # channel policy
            allowed_channel, policy_msg = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=world_id,
            )
            if not allowed_channel:
                await interaction.followup.send(policy_msg or "❌ لا يمكن استخدام الأمر في هذه القناة.", ephemeral=True)
                await _metric(self.services, "story.start.channel_blocked")
                return
            if policy_msg:
                await interaction.followup.send(policy_msg, ephemeral=True)

            # start part
            start_part_id = await self.services.story.get_start_part_id(world_id)
            part = await self.services.story.get_part(world_id, start_part_id)
            if not part:
                await interaction.followup.send("❌ لم يتم العثور على بداية العالم.", ephemeral=True)
                await _metric(self.services, "story.start.missing_start")
                return

            # save progress
            player["current_world"] = world_id
            progress = player.get("world_progress", {}) or {}
            progress[world_id] = start_part_id
            player["world_progress"] = progress
            await self.services.players.update_player(user_id, player)

            # save session + context
            await self.services.sessions.set_current_session(user_id, world_id=world_id, part_id=start_part_id)
            await self.services.sessions.set_session_context(
                user_id,
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
            )

            # IMPORTANT: view has timeout=None inside PersistentStoryView
            view = PersistentStoryView(
                services=self.services,
                user_id=user_id,
                world_id=world_id,
                part=part,
            )

            story_embed = build_story_embed(world_id=world_id, part=part)
            status_embed = build_status_embed(player=player, world_id=world_id)

            await interaction.followup.send(
                embeds=[story_embed, status_embed],
                view=view,
                ephemeral=False,
            )
            await _metric(self.services, "story.start.success")

        except Exception:
            logger.exception("start_command failed")
            await interaction.followup.send("❌ حدث خطأ أثناء بدء القصة.", ephemeral=True)

    @app_commands.command(name="استمر", description="⏩ أكمل رحلتك من آخر نقطة")
    async def continue_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            user_id = interaction.user.id
            player = await self.services.players.get_or_create_player(user_id, interaction.user.name)

            current_world = _normalize_world_id(player.get("current_world") or "fantasy")

            # channel policy
            allowed_channel, policy_msg = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=current_world,
            )
            if not allowed_channel:
                await interaction.followup.send(policy_msg or "❌ لا يمكن استخدام الأمر في هذه القناة.", ephemeral=True)
                await _metric(self.services, "story.continue.channel_blocked")
                return
            if policy_msg:
                await interaction.followup.send(policy_msg, ephemeral=True)

            # session first
            session = await self.services.sessions.get_current_session(user_id)
            if session:
                world_id = _normalize_world_id(str(session.get("world_id", current_world)))
                part_id = str(session.get("part_id"))
            else:
                world_id = current_world
                part_id = (player.get("world_progress", {}) or {}).get(world_id)
                if not part_id:
                    part_id = await self.services.story.get_start_part_id(world_id)

            # unlock check
            can_access, reason = await self.services.progression.can_access_world(player, world_id)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await _metric(self.services, "story.continue.locked")
                return

            part = await self.services.story.get_part(world_id, part_id)
            if not part:
                # recovery fallback
                start_part_id = await self.services.story.get_start_part_id(world_id)
                part = await self.services.story.get_part(world_id, start_part_id)
                if not part:
                    await interaction.followup.send("❌ تعذر استعادة التقدم. استخدم /ابدأ.", ephemeral=True)
                    await _metric(self.services, "story.continue.recovery_failed")
                    return
                part_id = start_part_id

            # keep session updated
            await self.services.sessions.set_current_session(user_id, world_id=world_id, part_id=part_id)
            await self.services.sessions.set_session_context(
                user_id,
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
            )

            view = PersistentStoryView(
                services=self.services,
                user_id=user_id,
                world_id=world_id,
                part=part,
            )

            story_embed = build_story_embed(world_id=world_id, part=part)
            status_embed = build_status_embed(player=player, world_id=world_id)

            await interaction.followup.send(
                embeds=[story_embed, status_embed],
                view=view,
                ephemeral=False,
            )
            await _metric(self.services, "story.continue.success")

        except Exception:
            logger.exception("continue_command failed")
            await interaction.followup.send("❌ حدث خطأ أثناء استكمال القصة.", ephemeral=True)

    @app_commands.command(name="عوالمي", description="🌍 عرض حالة العوالم وتقدمك فيها")
    async def worlds_command(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)

            embed = discord.Embed(
                title="🌍 عوالمك",
                description="حالة الفتح + الجزء الحالي + النهاية",
                color=WORLD_COLORS["general"],
                timestamp=datetime.now(timezone.utc),
            )

            for world_id in WORLD_ORDER:
                can_access, reason = await self.services.progression.can_access_world(player, world_id)
                progress = (player.get("world_progress", {}) or {}).get(world_id) or "-"
                ending = (player.get("endings", {}) or {}).get(world_id) or "-"
                state = "✅ مفتوح" if can_access else f"🔒 {reason}"

                embed.add_field(
                    name=f"{WORLD_EMOJIS[world_id]} {WORLD_NAMES_AR[world_id]}",
                    value=f"{state}\nالجزء الحالي: `{progress}`\nالنهاية: `{ending}`",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await _metric(self.services, "story.worlds.opened")

        except Exception:
            logger.exception("worlds_command failed")
            if interaction.response.is_done():
                await interaction.followup.send("❌ تعذر عرض حالة العوالم.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ تعذر عرض حالة العوالم.", ephemeral=True)

    @app_commands.command(name="احصائياتي", description="📊 عرض إحصائياتك الحالية")
    async def my_stats_command(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            world_id = _normalize_world_id(player.get("current_world") or "fantasy")
            embed = build_status_embed(player=player, world_id=world_id)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await _metric(self.services, "story.stats.opened")
        except Exception:
            logger.exception("my_stats_command failed")
            if interaction.response.is_done():
                await interaction.followup.send("❌ تعذر عرض الإحصائيات.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ تعذر عرض الإحصائيات.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    services = getattr(bot, "svc", None)
    if services is None:
        raise RuntimeError("Bot has no `svc` container attached.")
    await bot.add_cog(StoryCog(bot, services))
    logger.info("✅ StoryCog loaded (AR).")
