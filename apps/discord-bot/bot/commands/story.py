from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger("nexus.discord.commands.story")


# ============================================================
# Ports / Protocols
# ============================================================

class StoryServicePort(Protocol):
    async def get_start_part_id(self, world_id: str) -> str:
        ...

    async def get_part(self, world_id: str, part_id: str) -> Optional[dict]:
        ...

    async def is_ending(self, world_id: str, part_id: str) -> bool:
        ...

    async def get_ending(self, world_id: str, ending_id: str) -> Optional[dict]:
        ...

    async def list_worlds(self) -> list[str]:
        ...


class PlayerServicePort(Protocol):
    async def get_or_create_player(self, user_id: int, username: str) -> dict:
        ...

    async def update_player(self, user_id: int, updates: dict) -> None:
        ...

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
        ...


class ProgressionServicePort(Protocol):
    async def can_access_world(self, player: dict, world_id: str) -> tuple[bool, str]:
        ...

    async def apply_choice(
        self,
        *,
        player: dict,
        world_id: str,
        current_part: dict,
        choice_id: str,
    ) -> tuple[dict, str, dict]:
        ...


class SessionServicePort(Protocol):
    async def get_current_session(self, user_id: int) -> Optional[dict]:
        ...

    async def set_current_session(self, user_id: int, *, world_id: str, part_id: str) -> None:
        ...

    async def clear_current_session(self, user_id: int) -> None:
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
    "fantasy": 0x9B59B6,
    "retro": 0x3498DB,
    "future": 0xE74C3C,
    "alternate": 0x2ECC71,
    "general": 0x5865F2,
    "error": 0xE74C3C,
    "ok": 0x2ECC71,
    "warn": 0xF1C40F,
}


# ============================================================
# Helpers
# ============================================================

def _normalize_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    world_id = (world_id or "").strip().lower()
    return aliases.get(world_id, world_id)


def _world_label(world_id: str) -> str:
    return f"{WORLD_EMOJIS.get(world_id, '🌍')} {WORLD_NAMES_AR.get(world_id, world_id)}"


async def _safe_metric(services: ServicesPort, key: str, value: int = 1) -> None:
    try:
        await services.metrics.inc(key, value)
    except Exception:
        logger.exception("Metric failed: %s", key)


# ============================================================
# Button payload (persistent-friendly)
# ============================================================

@dataclass(frozen=True)
class StoryButtonPayload:
    """
    custom_id format:
      nx:v1:{user_id}:{world_id}:{part_id}:{choice_id}:{nonce}
    """
    user_id: int
    world_id: str
    part_id: str
    choice_id: str
    nonce: str

    def encode(self) -> str:
        return f"nx:v1:{self.user_id}:{self.world_id}:{self.part_id}:{self.choice_id}:{self.nonce}"

    @staticmethod
    def decode(value: str) -> Optional["StoryButtonPayload"]:
        try:
            parts = value.split(":")
            if len(parts) < 8:
                return None
            if parts[0] != "nx" or parts[1] != "v1":
                return None
            return StoryButtonPayload(
                user_id=int(parts[2]),
                world_id=_normalize_world_id(parts[3]),
                part_id=parts[4],
                choice_id=parts[5],
                nonce=":".join(parts[6:]),
            )
        except Exception:
            return None


def _new_payload(user_id: int, world_id: str, part_id: str, choice_id: str) -> StoryButtonPayload:
    return StoryButtonPayload(
        user_id=user_id,
        world_id=world_id,
        part_id=part_id,
        choice_id=choice_id,
        nonce=secrets.token_hex(4),
    )


# ============================================================
# Embeds
# ============================================================

def _build_story_embed(*, world_id: str, part: dict) -> discord.Embed:
    color = WORLD_COLORS.get(world_id, WORLD_COLORS["general"])
    title = str(part.get("title", "فصل غير معنون"))
    text = str(part.get("text", "لا يوجد نص لهذا الفصل."))
    part_id = str(part.get("id", "unknown"))

    embed = discord.Embed(
        title=f"{WORLD_EMOJIS.get(world_id, '🌍')} {title}",
        description=text,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    choices = part.get("choices", [])
    if choices:
        lines = []
        for i, c in enumerate(choices, 1):
            lines.append(f"{i}. {str(c.get('text', f'خيار {i}'))}")
        embed.add_field(name="🧭 الخيارات", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="🏁 نهاية", value="لا توجد خيارات إضافية.", inline=False)

    embed.set_footer(text=f"{WORLD_NAMES_AR.get(world_id, world_id)} • {part_id}")
    return embed


def _build_status_embed(*, player: dict, world_id: str) -> discord.Embed:
    embed = discord.Embed(
        title="📊 حالتك",
        color=WORLD_COLORS["general"],
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(name="المستوى", value=str(player.get("level", 1)), inline=True)
    embed.add_field(name="الخبرة", value=str(player.get("xp", 0)), inline=True)
    embed.add_field(name="الشظايا", value=str(player.get("shards", 0)), inline=True)
    embed.add_field(name="الفساد", value=str(player.get("corruption", 0)), inline=True)

    current_world = player.get("current_world") or world_id
    embed.add_field(
        name="العالم الحالي",
        value=_world_label(current_world),
        inline=True,
    )

    traits = player.get("traits", {})
    if isinstance(traits, dict) and traits:
        txt = "\n".join([f"• {k}: {v}" for k, v in traits.items()])
        embed.add_field(name="سمات الشخصية", value=txt[:1024], inline=False)

    endings = player.get("endings", {})
    if isinstance(endings, dict):
        txt = "\n".join([
            f"{WORLD_EMOJIS[w]} {WORLD_NAMES_AR[w]}: {endings.get(w) or '-'}"
            for w in WORLD_ORDER
        ])
        embed.add_field(name="النهايات", value=txt[:1024], inline=False)

    return embed


def _build_ending_embed(*, world_id: str, ending: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏁 {str(ending.get('title', 'نهاية'))}",
        description=str(ending.get("text", "وصلت إلى نهاية هذا المسار.")),
        color=WORLD_COLORS.get(world_id, WORLD_COLORS["ok"]),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="نوع النهاية", value=str(ending.get("type", "normal")), inline=True)

    rewards = ending.get("rewards", {})
    if rewards:
        embed.add_field(name="المكافآت", value=f"`{rewards}`"[:1024], inline=False)

    next_world = ending.get("next_world")
    if next_world:
        next_world = _normalize_world_id(str(next_world))
        embed.add_field(name="العالم التالي", value=_world_label(next_world), inline=True)

    return embed


# ============================================================
# Persistent Story View
# ============================================================

class PersistentStoryView(discord.ui.View):
    def __init__(
        self,
        *,
        services: ServicesPort,
        user_id: int,
        world_id: str,
        part: dict,
    ) -> None:
        super().__init__(timeout=None)
        self.services = services
        self.user_id = user_id
        self.world_id = _normalize_world_id(world_id)
        self.part = part
        self.part_id = str(part.get("id", "unknown"))

        for choice in part.get("choices", []):
            choice_id = str(choice.get("id", "unknown"))
            label = str(choice.get("text", "خيار"))[:80]
            payload = _new_payload(user_id, self.world_id, self.part_id, choice_id)

            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=payload.encode()[:100],
            )
            button.callback = self._on_choice_pressed  # type: ignore[assignment]
            self.add_item(button)

    async def _on_choice_pressed(self, interaction: discord.Interaction) -> None:
        try:
            data = interaction.data or {}
            payload = StoryButtonPayload.decode(str(data.get("custom_id", "")))

            if not payload:
                await interaction.response.send_message("❌ زر غير صالح.", ephemeral=True)
                return

            if interaction.user.id != payload.user_id:
                await interaction.response.send_message("❌ هذا الزر ليس لك.", ephemeral=True)
                return

            current_part = await self.services.story.get_part(payload.world_id, payload.part_id)
            if not current_part:
                await interaction.response.send_message(
                    "⚠️ هذا الجزء لم يعد متاحًا. استخدم `/استمر` لاستعادة الجلسة.",
                    ephemeral=True,
                )
                await _safe_metric(self.services, "story.button.dead_node")
                return

            player = await self.services.players.get_or_create_player(
                interaction.user.id,
                interaction.user.name
            )

            updated_player, next_part_id, effects = await self.services.progression.apply_choice(
                player=player,
                world_id=payload.world_id,
                current_part=current_part,
                choice_id=payload.choice_id,
            )

            # حفظ اللاعب
            await self.services.players.update_player(interaction.user.id, updated_player)

            # حفظ الجلسة + السياق (المطلوب منك تحديدًا)
            await self.services.sessions.set_current_session(
                interaction.user.id,
                world_id=payload.world_id,
                part_id=next_part_id,
            )
            await self.services.sessions.set_session_context(
                interaction.user.id,
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
            )

            # حفظ تاريخ القرار
            selected_choice_text = "غير معروف"
            for ch in current_part.get("choices", []):
                if str(ch.get("id")) == payload.choice_id:
                    selected_choice_text = str(ch.get("text", "غير معروف"))
                    break

            await self.services.players.save_choice_history(
                interaction.user.id,
                world_id=payload.world_id,
                from_part_id=payload.part_id,
                choice_id=payload.choice_id,
                choice_text=selected_choice_text,
                next_part_id=next_part_id,
                effects=effects,
            )

            # نهاية؟
            ending_data = await self.services.story.get_ending(payload.world_id, next_part_id)
            if ending_data:
                ending_embed = _build_ending_embed(world_id=payload.world_id, ending=ending_data)
                status_embed = _build_status_embed(player=updated_player, world_id=payload.world_id)

                await interaction.response.send_message(
                    embeds=[ending_embed, status_embed],
                    ephemeral=False,
                )
                await _safe_metric(self.services, "story.ending.reached")
                return

            # الجزء التالي
            next_part = await self.services.story.get_part(payload.world_id, next_part_id)
            if not next_part:
                await interaction.response.send_message(
                    "⚠️ الجزء التالي غير موجود. تم حفظ تقدمك، استخدم `/استمر`.",
                    ephemeral=True,
                )
                await _safe_metric(self.services, "story.button.missing_next")
                return

            next_view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
                world_id=payload.world_id,
                part=next_part,
            )
            story_embed = _build_story_embed(world_id=payload.world_id, part=next_part)
            status_embed = _build_status_embed(player=updated_player, world_id=payload.world_id)

            await interaction.response.send_message(
                embeds=[story_embed, status_embed],
                view=next_view,
                ephemeral=False,
            )
            await _safe_metric(self.services, "story.choice.applied")

        except Exception:
            logger.exception("خطأ أثناء معالجة زر القصة")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء تنفيذ الاختيار.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء تنفيذ الاختيار.", ephemeral=True)


# ============================================================
# Story Cog (Arabic Commands)
# ============================================================

class StoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot, services: ServicesPort) -> None:
        self.bot = bot
        self.services = services

    @app_commands.command(name="ابدأ", description="🚀 ابدأ رحلتك في عالم النيكسس")
    @app_commands.describe(العالم="اختر العالم الذي تريد البدء فيه (اختياري)")
    @app_commands.choices(العالم=[
        app_commands.Choice(name="🌲 عالم الفانتازيا", value="fantasy"),
        app_commands.Choice(name="📜 عالم الماضي", value="retro"),
        app_commands.Choice(name="🤖 عالم المستقبل", value="future"),
        app_commands.Choice(name="🌀 الواقع البديل", value="alternate"),
    ])
    async def start_command(
        self,
        interaction: discord.Interaction,
        العالم: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            target_world = _normalize_world_id(العالم or "fantasy")

            player = await self.services.players.get_or_create_player(
                interaction.user.id,
                interaction.user.name,
            )

            # فحص فتح العالم
            can_access, reason = await self.services.progression.can_access_world(player, target_world)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await _safe_metric(self.services, "story.start.locked")
                return

            # فحص سياسة القنوات
            allowed, policy_reason = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=target_world,
            )
            if not allowed:
                await interaction.followup.send(policy_reason or "❌ لا يمكنك استخدام الأمر هنا.", ephemeral=True)
                await _safe_metric(self.services, "story.start.channel_blocked")
                return

            if policy_reason:
                await interaction.followup.send(policy_reason, ephemeral=True)

            start_part_id = await self.services.story.get_start_part_id(target_world)
            start_part = await self.services.story.get_part(target_world, start_part_id)
            if not start_part:
                await interaction.followup.send(
                    f"❌ لم يتم العثور على بداية {_world_label(target_world)}.",
                    ephemeral=True,
                )
                await _safe_metric(self.services, "story.start.missing_start_part")
                return

            # حفظ تقدم اللاعب
            player["current_world"] = target_world
            progress = player.get("world_progress", {}) or {}
            progress[target_world] = start_part_id
            player["world_progress"] = progress
            await self.services.players.update_player(interaction.user.id, player)

            # حفظ الجلسة + السياق (هذا الإصلاح المطلوب)
            await self.services.sessions.set_current_session(
                interaction.user.id,
                world_id=target_world,
                part_id=start_part_id,
            )
            await self.services.sessions.set_session_context(
                interaction.user.id,
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
            )

            view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
                world_id=target_world,
                part=start_part,
            )
            story_embed = _build_story_embed(world_id=target_world, part=start_part)
            status_embed = _build_status_embed(player=player, world_id=target_world)

            await interaction.followup.send(
                embeds=[story_embed, status_embed],
                view=view,
                ephemeral=False,
            )
            await _safe_metric(self.services, "story.start.success")

        except Exception:
            logger.exception("فشل أمر /ابدأ")
            await interaction.followup.send("❌ حدث خطأ أثناء بدء القصة.", ephemeral=True)

    @app_commands.command(name="استمر", description="⏩ أكمل رحلتك من آخر نقطة")
    async def continue_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            player = await self.services.players.get_or_create_player(
                interaction.user.id,
                interaction.user.name,
            )
            current_world = _normalize_world_id(player.get("current_world") or "fantasy")

            # سياسة القنوات
            allowed, policy_reason = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=current_world,
            )
            if not allowed:
                await interaction.followup.send(policy_reason or "❌ لا يمكنك استخدام الأمر هنا.", ephemeral=True)
                await _safe_metric(self.services, "story.continue.channel_blocked")
                return

            if policy_reason:
                await interaction.followup.send(policy_reason, ephemeral=True)

            # الجلسة الحالية
            session = await self.services.sessions.get_current_session(interaction.user.id)
            if session:
                world_id = _normalize_world_id(str(session.get("world_id", current_world)))
                part_id = str(session.get("part_id"))
            else:
                world_id = current_world
                part_id = (player.get("world_progress", {}) or {}).get(world_id)
                if not part_id:
                    part_id = await self.services.story.get_start_part_id(world_id)

            # فحص الوصول
            can_access, reason = await self.services.progression.can_access_world(player, world_id)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await _safe_metric(self.services, "story.continue.locked")
                return

            part = await self.services.story.get_part(world_id, part_id)
            if not part:
                # fallback للبداية
                start_part_id = await self.services.story.get_start_part_id(world_id)
                part = await self.services.story.get_part(world_id, start_part_id)
                if not part:
                    await interaction.followup.send("❌ تعذر استعادة التقدم الحالي.", ephemeral=True)
                    await _safe_metric(self.services, "story.continue.recovery_failed")
                    return
                part_id = start_part_id

            # حفظ session + context
            await self.services.sessions.set_current_session(
                interaction.user.id,
                world_id=world_id,
                part_id=part_id,
            )
            await self.services.sessions.set_session_context(
                interaction.user.id,
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
            )

            view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
                world_id=world_id,
                part=part,
            )
            story_embed = _build_story_embed(world_id=world_id, part=part)
            status_embed = _build_status_embed(player=player, world_id=world_id)

            await interaction.followup.send(
                embeds=[story_embed, status_embed],
                view=view,
                ephemeral=False,
            )
            await _safe_metric(self.services, "story.continue.success")

        except Exception:
            logger.exception("فشل أمر /استمر")
            await interaction.followup.send("❌ حدث خطأ أثناء استكمال القصة.", ephemeral=True)

    @app_commands.command(name="عوالمي", description="🌍 عرض حالة العوالم وتقدمك فيها")
    async def worlds_command(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(
                interaction.user.id,
                interaction.user.name,
            )

            embed = discord.Embed(
                title="🌍 عوالمك",
                description="حالة الفتح + التقدم + النهاية",
                color=WORLD_COLORS["general"],
                timestamp=datetime.now(timezone.utc),
            )

            for world_id in WORLD_ORDER:
                can_access, reason = await self.services.progression.can_access_world(player, world_id)
                progress = (player.get("world_progress", {}) or {}).get(world_id) or "-"
                ending = (player.get("endings", {}) or {}).get(world_id) or "-"
                status = "✅ مفتوح" if can_access else f"🔒 {reason}"

                embed.add_field(
                    name=_world_label(world_id),
                    value=f"{status}\nالجزء الحالي: `{progress}`\nالنهاية: `{ending}`",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await _safe_metric(self.services, "story.worlds.opened")

        except Exception:
            logger.exception("فشل أمر /عوالمي")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض العوالم.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض العوالم.", ephemeral=True)

    @app_commands.command(name="احصائياتي", description="📊 عرض إحصائياتك الحالية")
    async def my_stats_command(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(
                interaction.user.id,
                interaction.user.name,
            )
            world_id = _normalize_world_id(player.get("current_world") or "fantasy")
            embed = _build_status_embed(player=player, world_id=world_id)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await _safe_metric(self.services, "story.stats.opened")
        except Exception:
            logger.exception("فشل أمر /احصائياتي")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض الإحصائيات.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض الإحصائيات.", ephemeral=True)


# ============================================================
# setup
# ============================================================

async def setup(bot: commands.Bot) -> None:
    services = getattr(bot, "svc", None)
    if services is None:
        raise RuntimeError("Bot has no `svc` container attached.")
    await bot.add_cog(StoryCog(bot, services))
    logger.info("✅ تم تحميل StoryCog (عربي + fixed).")
