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
# واجهات الخدمات (Ports)
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
        """
        يعيد:
            (اللاعب بعد التحديث, next_part_id, التأثيرات المطبقة)
        """
        ...


class SessionServicePort(Protocol):
    async def get_current_session(self, user_id: int) -> Optional[dict]:
        ...

    async def set_current_session(self, user_id: int, *, world_id: str, part_id: str) -> None:
        ...

    async def clear_current_session(self, user_id: int) -> None:
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
# ثوابت العرض
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
# payload الأزرار (ثابت + قابل للاستعادة)
# ============================================================

@dataclass(frozen=True)
class StoryButtonPayload:
    """
    custom_id موحد:
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
                world_id=parts[3],
                part_id=parts[4],
                choice_id=parts[5],
                nonce=":".join(parts[6:]),
            )
        except Exception:
            return None


def new_payload(user_id: int, world_id: str, part_id: str, choice_id: str) -> StoryButtonPayload:
    return StoryButtonPayload(
        user_id=user_id,
        world_id=world_id,
        part_id=part_id,
        choice_id=choice_id,
        nonce=secrets.token_hex(4),
    )


# ============================================================
# تصميم الـ Embeds (2 Embeds)
# ============================================================

def _build_story_embed(*, world_id: str, part: dict) -> discord.Embed:
    color = WORLD_COLORS.get(world_id, WORLD_COLORS["general"])
    title = part.get("title", "فصل غير معنون")
    text = part.get("text", "لا يوجد نص لهذا الفصل.")
    part_id = part.get("id", "unknown")

    embed = discord.Embed(
        title=f"{WORLD_EMOJIS.get(world_id, '🌍')} {title}",
        description=text,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    choices = part.get("choices", [])
    if choices:
        lines = []
        for idx, c in enumerate(choices, start=1):
            txt = c.get("text", f"خيار {idx}")
            lines.append(f"{idx}. {txt}")
        embed.add_field(name="🧭 الخيارات المتاحة", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="🏁 نهاية", value="لا توجد خيارات إضافية في هذا المسار.", inline=False)

    embed.set_footer(text=f"{WORLD_NAMES_AR.get(world_id, world_id)} • {part_id}")
    return embed


def _build_status_embed(*, player: dict, world_id: str) -> discord.Embed:
    embed = discord.Embed(
        title="📊 حالتك الحالية",
        color=WORLD_COLORS["general"],
        timestamp=datetime.now(timezone.utc),
    )

    level = player.get("level", 1)
    xp = player.get("xp", 0)
    shards = player.get("shards", 0)
    corruption = player.get("corruption", 0)
    current_world = player.get("current_world", "fantasy")

    embed.add_field(name="المستوى", value=str(level), inline=True)
    embed.add_field(name="الخبرة", value=str(xp), inline=True)
    embed.add_field(name="الشظايا", value=str(shards), inline=True)
    embed.add_field(name="الفساد", value=str(corruption), inline=True)
    embed.add_field(
        name="العالم الحالي",
        value=f"{WORLD_EMOJIS.get(current_world, '🌍')} {WORLD_NAMES_AR.get(current_world, current_world)}",
        inline=True,
    )

    traits = player.get("traits", {})
    if isinstance(traits, dict) and traits:
        trait_lines = [f"• {k}: {v}" for k, v in traits.items()]
        embed.add_field(name="سماتك", value="\n".join(trait_lines)[:1024], inline=False)

    endings = player.get("endings", {})
    if isinstance(endings, dict):
        ending_lines = []
        for w in WORLD_ORDER:
            ending_lines.append(f"{WORLD_EMOJIS[w]} {WORLD_NAMES_AR[w]}: {endings.get(w) or '-'}")
        embed.add_field(name="النهايات", value="\n".join(ending_lines)[:1024], inline=False)

    return embed


# ============================================================
# View أزرار القصة (Persistent)
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
        super().__init__(timeout=None)  # مهم: حتى الأزرار تظل ثابتة
        self.services = services
        self.user_id = user_id
        self.world_id = world_id
        self.part = part
        self.part_id = part.get("id", "unknown")

        for choice in part.get("choices", []):
            choice_id = str(choice.get("id", "unknown"))
            label = str(choice.get("text", "خيار"))[:80]
            payload = new_payload(user_id, world_id, self.part_id, choice_id)

            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=payload.encode()[:100],  # حد ديسكورد
            )
            button.callback = self._on_choice_pressed  # type: ignore[assignment]
            self.add_item(button)

    async def _on_choice_pressed(self, interaction: discord.Interaction) -> None:
        try:
            custom_id = str((interaction.data or {}).get("custom_id", ""))
            payload = StoryButtonPayload.decode(custom_id)
            if not payload:
                await interaction.response.send_message("❌ زر غير صالح.", ephemeral=True)
                return

            # منع استخدام أزرار لاعب آخر
            if interaction.user.id != payload.user_id:
                await interaction.response.send_message("❌ هذا الزر ليس لك.", ephemeral=True)
                return

            current_part = await self.services.story.get_part(payload.world_id, payload.part_id)
            if not current_part:
                await interaction.response.send_message(
                    "⚠️ هذا الجزء لم يعد متاحًا. استخدم `/استمر` لاستعادة الجلسة.",
                    ephemeral=True,
                )
                await self.services.metrics.inc("story.button.dead_node")
                return

            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)

            updated_player, next_part_id, effects = await self.services.progression.apply_choice(
                player=player,
                world_id=payload.world_id,
                current_part=current_part,
                choice_id=payload.choice_id,
            )

            # حفظ اللاعب + الجلسة
            await self.services.players.update_player(interaction.user.id, updated_player)
            await self.services.sessions.set_current_session(
                interaction.user.id,
                world_id=payload.world_id,
                part_id=next_part_id,
            )

            # حفظ التاريخ
            choice_text = "غير معروف"
            for c in current_part.get("choices", []):
                if str(c.get("id")) == payload.choice_id:
                    choice_text = str(c.get("text", "غير معروف"))
                    break

            await self.services.players.save_choice_history(
                interaction.user.id,
                world_id=payload.world_id,
                from_part_id=payload.part_id,
                choice_id=payload.choice_id,
                choice_text=choice_text,
                next_part_id=next_part_id,
                effects=effects,
            )

            # لو كان نهاية
            ending_data = await self.services.story.get_ending(payload.world_id, next_part_id)
            if ending_data:
                ending_embed = discord.Embed(
                    title=f"🏁 {ending_data.get('title', 'نهاية')}",
                    description=str(ending_data.get("text", "وصلت إلى نهاية هذا المسار.")),
                    color=WORLD_COLORS.get(payload.world_id, WORLD_COLORS["ok"]),
                    timestamp=datetime.now(timezone.utc),
                )
                ending_embed.add_field(
                    name="نوع النهاية",
                    value=str(ending_data.get("type", "normal")),
                    inline=True,
                )

                rewards = ending_data.get("rewards", {})
                if rewards:
                    ending_embed.add_field(
                        name="المكافآت",
                        value=f"`{rewards}`"[:1024],
                        inline=False,
                    )

                status_embed = _build_status_embed(player=updated_player, world_id=payload.world_id)
                await interaction.response.send_message(
                    embeds=[ending_embed, status_embed],
                    ephemeral=False,
                )
                await self.services.metrics.inc("story.ending.reached")
                return

            next_part = await self.services.story.get_part(payload.world_id, next_part_id)
            if not next_part:
                await interaction.response.send_message(
                    "⚠️ الجزء التالي غير موجود. تم حفظ تقدمك، استخدم `/استمر`.",
                    ephemeral=True,
                )
                await self.services.metrics.inc("story.button.missing_next")
                return

            view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
                world_id=payload.world_id,
                part=next_part,
            )
            story_embed = _build_story_embed(world_id=payload.world_id, part=next_part)
            status_embed = _build_status_embed(player=updated_player, world_id=payload.world_id)

            await interaction.response.send_message(
                embeds=[story_embed, status_embed],
                view=view,
                ephemeral=False,
            )
            await self.services.metrics.inc("story.choice.applied")

        except Exception:
            logger.exception("فشل معالجة ضغط زر القصة")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء تنفيذ الاختيار.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء تنفيذ الاختيار.", ephemeral=True)


# ============================================================
# Cog أوامر القصة العربية
# ============================================================

class StoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot, services: ServicesPort) -> None:
        self.bot = bot
        self.services = services

    @app_commands.command(name="ابدأ", description="🚀 ابدأ رحلتك في أحد العوالم")
    @app_commands.describe(العالم="اختر العالم الذي تريد البدء فيه")
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
            target_world = العالم or "fantasy"
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)

            # فحص الفتح
            can_access, reason = await self.services.progression.can_access_world(player, target_world)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await self.services.metrics.inc("story.start.locked")
                return

            # فحص سياسة القنوات
            allowed, policy_reason = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=target_world,
            )
            if not allowed:
                await interaction.followup.send(policy_reason or "❌ لا يمكنك استخدام هذا الأمر هنا.", ephemeral=True)
                await self.services.metrics.inc("story.start.channel_blocked")
                return

            if policy_reason:
                await interaction.followup.send(policy_reason, ephemeral=True)

            start_part_id = await self.services.story.get_start_part_id(target_world)
            start_part = await self.services.story.get_part(target_world, start_part_id)
            if not start_part:
                await interaction.followup.send(
                    f"❌ لم يتم العثور على بداية العالم `{target_world}`.",
                    ephemeral=True
                )
                await self.services.metrics.inc("story.start.missing_start")
                return

            # حفظ التقدم
            player["current_world"] = target_world
            progress = player.get("world_progress", {}) or {}
            progress[target_world] = start_part_id
            player["world_progress"] = progress

            await self.services.players.update_player(interaction.user.id, player)
            await self.services.sessions.set_current_session(
                interaction.user.id,
                world_id=target_world,
                part_id=start_part_id,
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
            await self.services.metrics.inc("story.start.success")

        except Exception:
            logger.exception("فشل أمر /ابدأ")
            await interaction.followup.send("❌ حدث خطأ أثناء بدء القصة.", ephemeral=True)

    @app_commands.command(name="استمر", description="⏩ أكمل رحلتك من آخر نقطة")
    async def continue_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            current_world = player.get("current_world") or "fantasy"

            # فحص سياسة القنوات
            allowed, policy_reason = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=current_world,
            )
            if not allowed:
                await interaction.followup.send(policy_reason or "❌ لا يمكنك استخدام هذا الأمر هنا.", ephemeral=True)
                await self.services.metrics.inc("story.continue.channel_blocked")
                return

            if policy_reason:
                await interaction.followup.send(policy_reason, ephemeral=True)

            session = await self.services.sessions.get_current_session(interaction.user.id)
            if session:
                world_id = str(session.get("world_id", current_world))
                part_id = str(session.get("part_id"))
            else:
                world_id = current_world
                part_id = (player.get("world_progress", {}) or {}).get(world_id)
                if not part_id:
                    part_id = await self.services.story.get_start_part_id(world_id)

            # فحص الوصول للعالم
            can_access, reason = await self.services.progression.can_access_world(player, world_id)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await self.services.metrics.inc("story.continue.locked")
                return

            part = await self.services.story.get_part(world_id, part_id)
            if not part:
                # fallback آمن
                start_part_id = await self.services.story.get_start_part_id(world_id)
                part = await self.services.story.get_part(world_id, start_part_id)
                if not part:
                    await interaction.followup.send("❌ تعذر استعادة تقدمك الحالي.", ephemeral=True)
                    await self.services.metrics.inc("story.continue.recovery_failed")
                    return

                part_id = start_part_id
                await self.services.sessions.set_current_session(
                    interaction.user.id,
                    world_id=world_id,
                    part_id=part_id,
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
            await self.services.metrics.inc("story.continue.success")

        except Exception:
            logger.exception("فشل أمر /استمر")
            await interaction.followup.send("❌ حدث خطأ أثناء استكمال القصة.", ephemeral=True)

    @app_commands.command(name="عوالمي", description="🌍 عرض حالة العوالم (الفتح + التقدم)")
    async def worlds_command(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            embed = discord.Embed(
                title="🌍 حالة عوالمك",
                description="ملخص الفتح والتقدم لكل عالم",
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
            await self.services.metrics.inc("story.worlds.opened")

        except Exception:
            logger.exception("فشل أمر /عوالمي")
            if interaction.response.is_done():
                await interaction.followup.send("❌ تعذر تحميل حالة العوالم.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ تعذر تحميل حالة العوالم.", ephemeral=True)

    @app_commands.command(name="احصائياتي", description="📊 عرض إحصائياتك الحالية")
    async def my_stats_command(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            world_id = player.get("current_world") or "fantasy"
            embed = _build_status_embed(player=player, world_id=world_id)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("story.stats.opened")
        except Exception:
            logger.exception("فشل أمر /احصائياتي")
            if interaction.response.is_done():
                await interaction.followup.send("❌ تعذر تحميل الإحصائيات.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ تعذر تحميل الإحصائيات.", ephemeral=True)


# ============================================================
# setup
# ============================================================

async def setup(bot: commands.Bot) -> None:
    """
    يتطلب وجود bot.svc وفيه الخدمات المطلوبة.
    """
    services = getattr(bot, "svc", None)
    if services is None:
        raise RuntimeError("Bot has no `svc` container attached.")
    await bot.add_cog(StoryCog(bot, services))
    logger.info("✅ تم تحميل StoryCog (عربي).")
