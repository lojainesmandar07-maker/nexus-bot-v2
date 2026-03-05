from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord


WORLD_COLORS = {
    "fantasy": 0x9B59B6,
    "retro": 0x3498DB,
    "future": 0xE74C3C,
    "alternate": 0x2ECC71,
    "general": 0x5865F2,
}

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


def _safe_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    world_id = (world_id or "").strip().lower()
    return aliases.get(world_id, world_id)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def build_story_embed(*, world_id: str, part: dict[str, Any]) -> discord.Embed:
    """
    Story-only embed:
    - عنوان القصة
    - نص المشهد
    - معاينة الخيارات
    - لا يحتوي على أزرار (الأزرار في View منفصل)
    """
    world_id = _safe_world_id(world_id)

    color = WORLD_COLORS.get(world_id, WORLD_COLORS["general"])
    world_emoji = WORLD_EMOJIS.get(world_id, "🌍")
    world_name = WORLD_NAMES_AR.get(world_id, world_id)

    part_id = str(part.get("id", "unknown"))
    title = str(part.get("title", "فصل جديد"))
    text = str(part.get("text", "لا يوجد نص لهذا الفصل."))

    embed = discord.Embed(
        title=f"{world_emoji} {title}",
        description=_truncate(text, 4096),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    location = part.get("location")
    if location:
        embed.add_field(name="📍 الموقع", value=_truncate(str(location), 1024), inline=False)

    choices = part.get("choices", [])
    if isinstance(choices, list) and choices:
        lines = []
        for idx, choice in enumerate(choices, start=1):
            c_emoji = str(choice.get("emoji", "•"))
            c_text = str(choice.get("text", f"خيار {idx}"))
            lines.append(f"{idx}. {c_emoji} {c_text}")
        embed.add_field(
            name="🧭 الخيارات المتاحة",
            value=_truncate("\n".join(lines), 1024),
            inline=False,
        )
    else:
        embed.add_field(
            name="🏁 الحالة",
            value="لا توجد خيارات إضافية في هذا الجزء.",
            inline=False,
        )

    tags = part.get("tags")
    if isinstance(tags, list) and tags:
        tags_text = " • ".join([str(t) for t in tags[:10]])
        embed.add_field(name="🏷️ وسوم", value=_truncate(tags_text, 1024), inline=False)

    embed.set_footer(text=f"{world_name} • {part_id} • اختر بحكمة")
    return embed
