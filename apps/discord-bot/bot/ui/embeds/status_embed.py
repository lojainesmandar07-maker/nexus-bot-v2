from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord


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

COLOR_GENERAL = 0x5865F2


def _safe_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    world_id = (world_id or "").strip().lower()
    return aliases.get(world_id, world_id)


def _bar(value: int, max_value: int = 100, length: int = 10) -> str:
    value = max(0, min(value, max_value))
    filled = int((value / max_value) * length) if max_value > 0 else 0
    return "🟪" * filled + "⬜" * (length - filled)


def _clamp_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def build_status_embed(*, player: dict[str, Any], world_id: str) -> discord.Embed:
    """
    Embed إحصائيات فقط (بدون نص القصة):
    - مستوى/XP/شظايا/فساد
    - تقدم العالم الحالي
    - سمات اللاعب
    - ملخص النهايات
    """
    world_id = _safe_world_id(world_id)
    current_world = _safe_world_id(player.get("current_world", world_id))

    level = _clamp_int(player.get("level", 1), 1)
    xp = _clamp_int(player.get("xp", 0), 0)
    shards = _clamp_int(player.get("shards", 0), 0)
    corruption = _clamp_int(player.get("corruption", 0), 0)

    embed = discord.Embed(
        title="📊 حالتك الحالية",
        color=COLOR_GENERAL,
        timestamp=datetime.now(timezone.utc),
    )

    # أساسي
    embed.add_field(name="المستوى", value=str(level), inline=True)
    embed.add_field(name="الخبرة", value=str(xp), inline=True)
    embed.add_field(name="الشظايا", value=str(shards), inline=True)

    # الفساد بشريط
    embed.add_field(
        name="الفساد",
        value=f"{_bar(corruption)} `{corruption}%`",
        inline=False,
    )

    embed.add_field(
        name="العالم الحالي",
        value=f"{WORLD_EMOJIS.get(current_world, '🌍')} {WORLD_NAMES_AR.get(current_world, current_world)}",
        inline=True,
    )

    # الجزء الحالي
    world_progress = player.get("world_progress", {}) or {}
    current_part = world_progress.get(current_world) or "لم يبدأ"
    embed.add_field(name="الجزء الحالي", value=f"`{current_part}`", inline=True)

    # السمات
    traits = player.get("traits", {}) or {}
    if isinstance(traits, dict) and traits:
        lines = [f"• {k}: {v}" for k, v in traits.items()]
        embed.add_field(name="سمات الشخصية", value="\n".join(lines)[:1024], inline=False)

    # نهايات
    endings = player.get("endings", {}) or {}
    if isinstance(endings, dict):
        lines = []
        for wid in ["fantasy", "retro", "future", "alternate"]:
            lines.append(f"{WORLD_EMOJIS[wid]} {WORLD_NAMES_AR[wid]}: {endings.get(wid) or '-'}")
        embed.add_field(name="النهايات", value="\n".join(lines)[:1024], inline=False)

    embed.set_footer(text="إحصائيات اللاعب • يتم التحديث مع كل اختيار")
    return embed
