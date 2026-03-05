from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from discord.ext import commands


logger = logging.getLogger("nexus.discord.registrar")


@dataclass(frozen=True)
class ExtensionSpec:
    module: str
    required: bool = True


class CommandRegistrar:
    """
    مسجل إضافات (Extensions) البوت.
    - يحمل أوامر story/admin/help
    - يدعم required/optional
    """

    def __init__(self, extensions: Sequence[ExtensionSpec] | None = None) -> None:
        self.extensions = list(extensions or [
            ExtensionSpec("bot.commands.story", required=True),
            ExtensionSpec("bot.commands.admin", required=True),
            ExtensionSpec("bot.commands.help", required=True),
        ])

    async def register(self, bot: commands.Bot) -> None:
        loaded = 0
        failed_required: list[str] = []
        failed_optional: list[str] = []

        for ext in self.extensions:
            try:
                await bot.load_extension(ext.module)
                loaded += 1
                logger.info("✅ تم تحميل الإضافة: %s", ext.module)
            except Exception as exc:
                if ext.required:
                    failed_required.append(ext.module)
                    logger.exception("❌ فشل تحميل إضافة مطلوبة: %s | %s", ext.module, exc)
                else:
                    failed_optional.append(ext.module)
                    logger.warning("⚠️ فشل تحميل إضافة اختيارية: %s | %s", ext.module, exc)

        logger.info("تم تحميل %s/%s إضافات", loaded, len(self.extensions))

        if failed_optional:
            logger.warning("إضافات اختيارية فشلت: %s", ", ".join(failed_optional))

        if failed_required:
            raise RuntimeError(
                f"فشل تحميل إضافات مطلوبة: {', '.join(failed_required)}"
            )
