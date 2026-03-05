from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol, Optional

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger("nexus.discord.commands.help")


# ============================================================
# Ports (اختياري، فقط إذا أردت ربطه بخدماتك)
# ============================================================

class MetricsPort(Protocol):
    async def inc(self, key: str, value: int = 1) -> None:
        ...


class ServicesPort(Protocol):
    metrics: MetricsPort


# ============================================================
# ثوابت
# ============================================================

WORLD_EMOJIS = {
    "fantasy": "🌲",
    "retro": "📜",
    "future": "🤖",
    "alternate": "🌀",
}

WORLD_NAMES_AR = {
    "fantasy": "عالم الفانتازيا",
    "retro": "عالم الماضي",
    "future": "عالم المستقبل",
    "alternate": "الواقع البديل",
}

CMD_COLOR = 0x5865F2
WARN_COLOR = 0xF1C40F
OK_COLOR = 0x2ECC71


# ============================================================
# أدوات مساعدة
# ============================================================

def _embed(title: str, description: str, color: int = CMD_COLOR) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )


def _is_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None


# ============================================================
# Help Cog
# ============================================================

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot, services: Optional[ServicesPort] = None) -> None:
        self.bot = bot
        self.services = services

    async def _metric(self, key: str) -> None:
        if self.services is None:
            return
        try:
            await self.services.metrics.inc(key)
        except Exception:
            logger.exception("Failed to send metric: %s", key)

    # --------------------------------------------------------
    # /مساعدة
    # --------------------------------------------------------
    @app_commands.command(name="مساعدة", description="📚 عرض دليل أوامر البوت")
    async def help_command(self, interaction: discord.Interaction) -> None:
        try:
            embed = _embed(
                "📚 دليل بوت النيكسس",
                "مرحباً بك! هذه أهم الأوامر للبدء بسرعة.",
            )

            embed.add_field(
                name="🚀 أوامر القصة",
                value=(
                    "• `/ابدأ` بدء رحلة جديدة (مع اختيار عالم)\n"
                    "• `/استمر` متابعة من آخر نقطة\n"
                    "• `/عوالمي` عرض حالة العوالم\n"
                    "• `/احصائياتي` عرض إحصائياتك"
                ),
                inline=False,
            )

            embed.add_field(
                name="🛠️ أوامر الإدارة (للمشرف)",
                value=(
                    "• `/تعيين_عالم` تعيين قناة لكل عالم\n"
                    "• `/عرض_تعيين_العوالم` عرض قنوات العوالم\n"
                    "• `/وضع_القنوات` تغيير السياسة (صارم/مرن/معطّل)\n"
                    "• `/حالة_الإدارة` فحص سريع للإعدادات"
                ),
                inline=False,
            )

            embed.add_field(
                name="💡 ملاحظات مهمة",
                value=(
                    "• اختياراتك تؤثر على القصة والنهايات.\n"
                    "• إذا زر ما اشتغل: استخدم `/استمر` لاستعادة الجلسة.\n"
                    "• يفضّل اللعب في قنوات العوالم المعيّنة من الإدارة."
                ),
                inline=False,
            )

            embed.set_footer(text="Nexus Story Bot • إصدار عربي")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self._metric("help.main.opened")
        except Exception:
            logger.exception("Failed /مساعدة")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض المساعدة.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض المساعدة.", ephemeral=True)

    # --------------------------------------------------------
    # /كيف_ابدأ
    # --------------------------------------------------------
    @app_commands.command(name="كيف_ابدأ", description="🧭 شرح سريع لبدء اللعب")
    async def quick_start_command(self, interaction: discord.Interaction) -> None:
        try:
            embed = _embed(
                "🧭 كيف تبدأ؟",
                "اتبع هذه الخطوات بالترتيب:",
                OK_COLOR,
            )
            embed.add_field(
                name="1) ابدأ الرحلة",
                value="اكتب `/ابدأ` ثم اختر العالم (أو اتركه فارغًا).",
                inline=False,
            )
            embed.add_field(
                name="2) اختر قرارك",
                value="سيظهر لك **Embed القصة** + **Embed الإحصائيات** مع أزرار الخيارات.",
                inline=False,
            )
            embed.add_field(
                name="3) أكمل عند الانقطاع",
                value="إذا انقطع كل شيء أو صار خطأ، اكتب `/استمر`.",
                inline=False,
            )
            embed.add_field(
                name="4) راقب تقدمك",
                value="اكتب `/عوالمي` و `/احصائياتي` لمتابعة التقدم.",
                inline=False,
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self._metric("help.quick_start.opened")
        except Exception:
            logger.exception("Failed /كيف_ابدأ")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض الشرح.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض الشرح.", ephemeral=True)

    # --------------------------------------------------------
    # /حل_المشاكل
    # --------------------------------------------------------
    @app_commands.command(name="حل_المشاكل", description="🧯 حلول أشهر المشاكل")
    async def troubleshooting_command(self, interaction: discord.Interaction) -> None:
        try:
            embed = _embed(
                "🧯 حل المشاكل",
                "إذا واجهت مشكلة، جرّب الحلول التالية:",
                WARN_COLOR,
            )

            embed.add_field(
                name="🔘 زر لا يعمل",
                value=(
                    "1. تأكد أنك أنت صاحب الجلسة.\n"
                    "2. اكتب `/استمر` لإعادة تحميل آخر جزء.\n"
                    "3. إذا استمرت المشكلة، أبلغ الإدارة (قد يكون جزء قصة مفقود)."
                ),
                inline=False,
            )

            embed.add_field(
                name="🔒 عالم مقفل",
                value=(
                    "• تحقق من المستوى المطلوب.\n"
                    "• أكمل نهاية العالم السابق.\n"
                    "• اكتب `/عوالمي` لمعرفة السبب بدقة."
                ),
                inline=False,
            )

            embed.add_field(
                name="📍 الأمر لا يعمل في هذه القناة",
                value=(
                    "• قد تكون سياسة القنوات مفعلة.\n"
                    "• جرّب قناة العالم المخصصة.\n"
                    "• الإدارة يمكنها تعديل الوضع عبر `/وضع_القنوات`."
                ),
                inline=False,
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self._metric("help.troubleshooting.opened")
        except Exception:
            logger.exception("Failed /حل_المشاكل")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض حلول المشاكل.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض حلول المشاكل.", ephemeral=True)

    # --------------------------------------------------------
    # /شرح_العوالم
    # --------------------------------------------------------
    @app_commands.command(name="شرح_العوالم", description="🌍 شرح العوالم ومسار فتحها")
    async def worlds_guide_command(self, interaction: discord.Interaction) -> None:
        try:
            embed = _embed(
                "🌍 شرح العوالم",
                "هذه العوالم الأربعة ومسار فتحها:",
            )

            embed.add_field(
                name=f"{WORLD_EMOJIS['fantasy']} {WORLD_NAMES_AR['fantasy']}",
                value="متاح دائمًا كبداية.",
                inline=False,
            )
            embed.add_field(
                name=f"{WORLD_EMOJIS['retro']} {WORLD_NAMES_AR['retro']}",
                value="يفتح بعد إنهاء الفانتازيا + مستوى مناسب.",
                inline=False,
            )
            embed.add_field(
                name=f"{WORLD_EMOJIS['future']} {WORLD_NAMES_AR['future']}",
                value="يفتح بعد إنهاء الماضي + مستوى مناسب.",
                inline=False,
            )
            embed.add_field(
                name=f"{WORLD_EMOJIS['alternate']} {WORLD_NAMES_AR['alternate']}",
                value="يفتح بعد إنهاء المستقبل + مستوى مناسب.",
                inline=False,
            )

            embed.set_footer(text="اكتب /عوالمي لتعرف وضعك الحالي في كل عالم")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self._metric("help.worlds_guide.opened")
        except Exception:
            logger.exception("Failed /شرح_العوالم")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض شرح العوالم.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض شرح العوالم.", ephemeral=True)

    # --------------------------------------------------------
    # /سياسة_القنوات
    # --------------------------------------------------------
    @app_commands.command(name="سياسة_القنوات", description="🧭 شرح أوضاع سياسة القنوات")
    async def policy_guide_command(self, interaction: discord.Interaction) -> None:
        try:
            embed = _embed("🧭 سياسة القنوات", "أوضاع سياسة القنوات في البوت:")

            embed.add_field(
                name="🔒 صارم",
                value="لا يسمح بأوامر القصة إلا في القناة المحددة لكل عالم.",
                inline=False,
            )
            embed.add_field(
                name="⚠️ مرن",
                value="يسمح بالأوامر في أي قناة مع تنبيه بالقناة الموصى بها.",
                inline=False,
            )
            embed.add_field(
                name="✅ معطّل",
                value="لا يوجد تقييد على القنوات.",
                inline=False,
            )

            if _is_guild(interaction):
                embed.set_footer(text="للمشرف: استخدم /وضع_القنوات لتغيير الوضع")
            else:
                embed.set_footer(text="هذه الميزة مفيدة أكثر داخل السيرفرات")

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self._metric("help.policy_guide.opened")
        except Exception:
            logger.exception("Failed /سياسة_القنوات")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء عرض سياسة القنوات.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء عرض سياسة القنوات.", ephemeral=True)


# ============================================================
# setup
# ============================================================

async def setup(bot: commands.Bot) -> None:
    """
    دعم طريقتين:
    1) bot.svc موجود وفيه metrics
    2) بدون خدمات (يعمل عادي لكن بدون metrics)
    """
    services = getattr(bot, "svc", None)
    await bot.add_cog(HelpCog(bot, services))
    logger.info("✅ تم تحميل HelpCog (عربي).")
