from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Protocol

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger("nexus.discord.commands.admin")


# ============================================================
# Ports / Protocols
# ============================================================

class ChannelPolicyAdminPort(Protocol):
    async def set_world_channel(self, guild_id: int, world_id: str, channel_id: int, set_by: int) -> None:
        ...

    async def get_world_channel(self, guild_id: int, world_id: str) -> Optional[int]:
        ...

    async def get_guild_world_channels(self, guild_id: int) -> dict[str, int]:
        ...

    async def set_policy_mode(self, guild_id: int, mode: str, set_by: int) -> None:
        ...

    async def get_policy_mode(self, guild_id: int) -> str:
        ...


class StoryServicePort(Protocol):
    async def list_worlds(self) -> list[str]:
        ...

    async def get_start_part_id(self, world_id: str) -> str:
        ...


class SessionServicePort(Protocol):
    async def get_active_sessions_count(self, guild_id: Optional[int] = None) -> int:
        ...


class MetricsPort(Protocol):
    async def inc(self, key: str, value: int = 1) -> None:
        ...


class ServicesPort(Protocol):
    policy_admin: ChannelPolicyAdminPort
    story: StoryServicePort
    sessions: SessionServicePort
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

MODE_NAMES_AR = {
    "strict": "صارم",
    "soft": "مرن",
    "off": "معطّل",
}

VALID_MODES = {"strict", "soft", "off"}


# ============================================================
# Helpers
# ============================================================

def _is_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    member = interaction.user
    if isinstance(member, discord.Member):
        return member.guild_permissions.administrator
    return False


def _guild_only(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None


def _safe_world_label(world_id: str) -> str:
    return f"{WORLD_EMOJIS.get(world_id, '🌍')} {WORLD_NAMES_AR.get(world_id, world_id)}"


def _mk_embed(title: str, description: str, color: int = 0x5865F2) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================
# Admin Cog (Arabic)
# ============================================================

class AdminCog(commands.Cog):
    """
    أوامر الإدارة الخاصة بتعيين قنوات العوالم وسياسة القنوات.
    """

    def __init__(self, bot: commands.Bot, services: ServicesPort) -> None:
        self.bot = bot
        self.services = services

    # --------------------------------------------------------
    # /تعيين_عالم
    # --------------------------------------------------------
    @app_commands.command(name="تعيين_عالم", description="⚙️ تعيين قناة قصة لعالم محدد")
    @app_commands.describe(
        العالم="العالم المطلوب",
        القناة="القناة التي تريد إرسال قصة هذا العالم إليها"
    )
    @app_commands.choices(العالم=[
        app_commands.Choice(name="🌲 عالم الفانتازيا", value="fantasy"),
        app_commands.Choice(name="📜 عالم الماضي", value="retro"),
        app_commands.Choice(name="🤖 عالم المستقبل", value="future"),
        app_commands.Choice(name="🌀 الواقع البديل", value="alternate"),
    ])
    async def set_world_channel_command(
        self,
        interaction: discord.Interaction,
        العالم: str,
        القناة: discord.TextChannel,
    ) -> None:
        try:
            if not _guild_only(interaction):
                await interaction.response.send_message("❌ هذا الأمر يعمل داخل السيرفر فقط.", ephemeral=True)
                return

            if not _is_admin(interaction):
                await interaction.response.send_message("❌ هذا الأمر للمشرفين فقط.", ephemeral=True)
                return

            world_id = العالم
            if world_id not in WORLD_ORDER:
                await interaction.response.send_message("❌ عالم غير صالح.", ephemeral=True)
                return

            # فحص صلاحيات البوت في القناة المستهدفة
            me = interaction.guild.me if interaction.guild else None
            if me is None:
                await interaction.response.send_message("❌ تعذر التحقق من صلاحيات البوت.", ephemeral=True)
                return

            perms = القناة.permissions_for(me)
            if not perms.send_messages or not perms.embed_links:
                await interaction.response.send_message(
                    "❌ البوت يحتاج صلاحية إرسال الرسائل والـ Embed في القناة المحددة.",
                    ephemeral=True
                )
                return

            await self.services.policy_admin.set_world_channel(
                guild_id=interaction.guild.id,
                world_id=world_id,
                channel_id=القناة.id,
                set_by=interaction.user.id,
            )

            embed = _mk_embed(
                title="✅ تم حفظ تعيين العالم",
                description=(
                    f"تم تعيين {_safe_world_label(world_id)} إلى القناة: {القناة.mention}\n"
                    "سيتم فرض السياسة حسب وضع القنوات الحالي."
                ),
                color=0x2ECC71,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("admin.world_channel.set")

        except Exception:
            logger.exception("فشل أمر /تعيين_عالم")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء حفظ الإعداد.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء حفظ الإعداد.", ephemeral=True)

    # --------------------------------------------------------
    # /عرض_تعيين_العوالم
    # --------------------------------------------------------
    @app_commands.command(name="عرض_تعيين_العوالم", description="🗺️ عرض قنوات العوالم الحالية")
    async def show_world_mapping_command(self, interaction: discord.Interaction) -> None:
        try:
            if not _guild_only(interaction):
                await interaction.response.send_message("❌ هذا الأمر يعمل داخل السيرفر فقط.", ephemeral=True)
                return

            if not _is_admin(interaction):
                await interaction.response.send_message("❌ هذا الأمر للمشرفين فقط.", ephemeral=True)
                return

            guild_id = interaction.guild.id
            mapping = await self.services.policy_admin.get_guild_world_channels(guild_id)
            mode = await self.services.policy_admin.get_policy_mode(guild_id)

            embed = _mk_embed(
                title="🗺️ إعدادات قنوات العوالم",
                description=f"وضع السياسة الحالي: **{MODE_NAMES_AR.get(mode, mode)}**",
                color=0x3498DB,
            )

            for world_id in WORLD_ORDER:
                channel_id = mapping.get(world_id)
                channel = interaction.guild.get_channel(channel_id) if channel_id else None
                channel_text = channel.mention if channel else "غير معيّنة"

                # start part check (helpful admin diagnostic)
                try:
                    start_part = await self.services.story.get_start_part_id(world_id)
                except Exception:
                    start_part = "غير معروف"

                embed.add_field(
                    name=_safe_world_label(world_id),
                    value=f"القناة: {channel_text}\nبداية العالم: `{start_part}`",
                    inline=False,
                )

            embed.set_footer(text="استخدم /تعيين_عالم للتعديل • /وضع_القنوات لتغيير السلوك")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("admin.world_channel.show")

        except Exception:
            logger.exception("فشل أمر /عرض_تعيين_العوالم")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء جلب الإعدادات.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء جلب الإعدادات.", ephemeral=True)

    # --------------------------------------------------------
    # /وضع_القنوات
    # --------------------------------------------------------
    @app_commands.command(name="وضع_القنوات", description="🧭 تغيير وضع سياسة القنوات (صارم/مرن/معطّل)")
    @app_commands.describe(الوضع="اختر طريقة فرض القنوات")
    @app_commands.choices(الوضع=[
        app_commands.Choice(name="🔒 صارم (إلزامي)", value="strict"),
        app_commands.Choice(name="⚠️ مرن (تنبيه فقط)", value="soft"),
        app_commands.Choice(name="✅ معطّل (بدون فرض)", value="off"),
    ])
    async def set_policy_mode_command(
        self,
        interaction: discord.Interaction,
        الوضع: str,
    ) -> None:
        try:
            if not _guild_only(interaction):
                await interaction.response.send_message("❌ هذا الأمر يعمل داخل السيرفر فقط.", ephemeral=True)
                return

            if not _is_admin(interaction):
                await interaction.response.send_message("❌ هذا الأمر للمشرفين فقط.", ephemeral=True)
                return

            mode = الوضع.strip().lower()
            if mode not in VALID_MODES:
                await interaction.response.send_message("❌ وضع غير صالح.", ephemeral=True)
                return

            await self.services.policy_admin.set_policy_mode(
                guild_id=interaction.guild.id,
                mode=mode,
                set_by=interaction.user.id,
            )

            mode_ar = MODE_NAMES_AR.get(mode, mode)
            explain = {
                "strict": "لن يُسمح باستخدام أوامر القصة إلا في قناة العالم المحددة.",
                "soft": "سيسمح بالاستخدام مع إرسال تنبيه بالقناة الموصى بها.",
                "off": "لن يتم فرض أي قيد على قناة الاستخدام.",
            }.get(mode, "")

            embed = _mk_embed(
                title="✅ تم تحديث وضع القنوات",
                description=f"الوضع الجديد: **{mode_ar}**\n{explain}",
                color=0x2ECC71,
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("admin.policy_mode.set")

        except Exception:
            logger.exception("فشل أمر /وضع_القنوات")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء تحديث الوضع.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء تحديث الوضع.", ephemeral=True)

    # --------------------------------------------------------
    # /حالة_الإدارة
    # --------------------------------------------------------
    @app_commands.command(name="حالة_الإدارة", description="📈 فحص سريع لحالة إعدادات الإدارة")
    async def admin_health_command(self, interaction: discord.Interaction) -> None:
        try:
            if not _guild_only(interaction):
                await interaction.response.send_message("❌ هذا الأمر يعمل داخل السيرفر فقط.", ephemeral=True)
                return

            if not _is_admin(interaction):
                await interaction.response.send_message("❌ هذا الأمر للمشرفين فقط.", ephemeral=True)
                return

            guild_id = interaction.guild.id
            mapping = await self.services.policy_admin.get_guild_world_channels(guild_id)
            mode = await self.services.policy_admin.get_policy_mode(guild_id)
            sessions_count = await self.services.sessions.get_active_sessions_count(guild_id)

            assigned = len([w for w in WORLD_ORDER if mapping.get(w)])
            missing = len(WORLD_ORDER) - assigned

            color = 0x2ECC71 if missing == 0 else 0xF1C40F
            embed = _mk_embed(
                title="📊 حالة الإدارة",
                description="ملخص سريع قبل الإطلاق أو الاختبار.",
                color=color,
            )

            embed.add_field(name="وضع السياسة", value=MODE_NAMES_AR.get(mode, mode), inline=True)
            embed.add_field(name="عوالم معيّنة", value=f"{assigned}/{len(WORLD_ORDER)}", inline=True)
            embed.add_field(name="عوالم بدون قناة", value=str(missing), inline=True)
            embed.add_field(name="جلسات نشطة", value=str(sessions_count), inline=True)

            if missing > 0 and mode == "strict":
                embed.add_field(
                    name="تنبيه",
                    value="الوضع صارم وهناك عوالم بلا قناة. اللاعبون قد يُمنعون من البدء بهذه العوالم.",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("admin.health.opened")

        except Exception:
            logger.exception("فشل أمر /حالة_الإدارة")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء فحص الحالة.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء فحص الحالة.", ephemeral=True)


# ============================================================
# setup
# ============================================================

async def setup(bot: commands.Bot) -> None:
    """
    يتطلب وجود bot.svc وفيه:
      - policy_admin
      - story
      - sessions
      - metrics
    """
    services = getattr(bot, "svc", None)
    if services is None:
        raise RuntimeError("Bot has no `svc` container attached.")
    await bot.add_cog(AdminCog(bot, services))
    logger.info("✅ تم تحميل AdminCog (عربي).")
