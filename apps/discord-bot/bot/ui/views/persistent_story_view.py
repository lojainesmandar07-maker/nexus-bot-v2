from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol, Any

import discord

from bot.ui.embeds.story_embed import build_story_embed
from bot.ui.embeds.status_embed import build_status_embed


logger = logging.getLogger("nexus.discord.ui.persistent_story_view")


# ============================================================
# Ports
# ============================================================

class StoryServicePort(Protocol):
    async def get_part(self, world_id: str, part_id: str) -> Optional[dict]:
        ...

    async def get_ending(self, world_id: str, ending_id: str) -> Optional[dict]:
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


class MetricsPort(Protocol):
    async def inc(self, key: str, value: int = 1) -> None:
        ...


class ServicesPort(Protocol):
    story: StoryServicePort
    players: PlayerServicePort
    progression: ProgressionServicePort
    sessions: SessionServicePort
    metrics: MetricsPort


# ============================================================
# Helpers
# ============================================================

def _normalize_world_id(world_id: str) -> str:
    aliases = {"past": "retro", "alt": "alternate"}
    world_id = (world_id or "").strip().lower()
    return aliases.get(world_id, world_id)


@dataclass(frozen=True)
class StoryButtonPayload:
    """
    custom_id deterministic format:
    nx:v1:{user_id}:{world_id}:{part_id}:{choice_id}:{slot}

    - user ownership enforced
    - no random nonce needed هنا لأننا نستخدم slot/choice ثابت
    """
    user_id: int
    world_id: str
    part_id: str
    choice_id: str
    slot: int

    def encode(self) -> str:
        return f"nx:v1:{self.user_id}:{self.world_id}:{self.part_id}:{self.choice_id}:{self.slot}"

    @staticmethod
    def decode(value: str) -> Optional["StoryButtonPayload"]:
        try:
            p = value.split(":")
            if len(p) != 7:
                return None
            if p[0] != "nx" or p[1] != "v1":
                return None
            return StoryButtonPayload(
                user_id=int(p[2]),
                world_id=_normalize_world_id(p[3]),
                part_id=p[4],
                choice_id=p[5],
                slot=int(p[6]),
            )
        except Exception:
            return None


async def _metric(services: ServicesPort, key: str, value: int = 1) -> None:
    try:
        await services.metrics.inc(key, value)
    except Exception:
        logger.exception("metric failed: %s", key)


# ============================================================
# Persistent View
# ============================================================

class PersistentStoryView(discord.ui.View):
    """
    View دائم:
    - timeout=None
    - custom_id ثابت
    - ownership check
    - fallback واضح إذا الجزء/الخيار غير موجود
    """

    def __init__(
        self,
        *,
        services: ServicesPort,
        user_id: int,
        world_id: str,
        part: dict[str, Any],
    ) -> None:
        super().__init__(timeout=None)
        self.services = services
        self.user_id = int(user_id)
        self.world_id = _normalize_world_id(world_id)
        self.part = part
        self.part_id = str(part.get("id", "unknown"))

        choices = part.get("choices", []) or []
        for idx, c in enumerate(choices):
            choice_id = str(c.get("id", f"choice_{idx+1}"))
            label = str(c.get("text", f"خيار {idx+1}"))[:80]

            payload = StoryButtonPayload(
                user_id=self.user_id,
                world_id=self.world_id,
                part_id=self.part_id,
                choice_id=choice_id,
                slot=idx,
            )

            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=payload.encode()[:100],  # limit safe
            )
            button.callback = self._on_choice_click  # type: ignore[assignment]
            self.add_item(button)

    async def _on_choice_click(self, interaction: discord.Interaction) -> None:
        try:
            custom_id = str((interaction.data or {}).get("custom_id", ""))
            payload = StoryButtonPayload.decode(custom_id)
            if not payload:
                await interaction.response.send_message("❌ زر غير صالح.", ephemeral=True)
                return

            # ownership
            if interaction.user.id != payload.user_id:
                await interaction.response.send_message("❌ هذا الزر ليس لك.", ephemeral=True)
                return

            # load current part from source of truth
            current_part = await self.services.story.get_part(payload.world_id, payload.part_id)
            if not current_part:
                await interaction.response.send_message(
                    "⚠️ هذا الجزء لم يعد متاحًا. استخدم `/استمر` لاستعادة الجلسة.",
                    ephemeral=True,
                )
                await _metric(self.services, "story.button.dead_part")
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

            await self.services.players.update_player(interaction.user.id, updated_player)

            # save session + context (مهم لإعادة view و admin stats)
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

            # save history
            selected_text = "غير معروف"
            for c in current_part.get("choices", []):
                if str(c.get("id")) == payload.choice_id:
                    selected_text = str(c.get("text", "غير معروف"))
                    break

            await self.services.players.save_choice_history(
                interaction.user.id,
                world_id=payload.world_id,
                from_part_id=payload.part_id,
                choice_id=payload.choice_id,
                choice_text=selected_text,
                next_part_id=next_part_id,
                effects=effects,
            )

            # ending?
            ending_data = await self.services.story.get_ending(payload.world_id, next_part_id)
            if ending_data:
                ending_embed = discord.Embed(
                    title=f"🏁 {ending_data.get('title', 'نهاية')}",
                    description=str(ending_data.get("text", "وصلت إلى نهاية المسار.")),
                    color=0x2ECC71,
                )
                status_embed = build_status_embed(player=updated_player, world_id=payload.world_id)
                await interaction.response.send_message(
                    embeds=[ending_embed, status_embed],
                    ephemeral=False,
                )
                await _metric(self.services, "story.ending.reached")
                return

            # next part
            next_part = await self.services.story.get_part(payload.world_id, next_part_id)
            if not next_part:
                await interaction.response.send_message(
                    "⚠️ الجزء التالي غير موجود. تم حفظ تقدمك. استخدم `/استمر`.",
                    ephemeral=True,
                )
                await _metric(self.services, "story.button.missing_next")
                return

            next_view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
                world_id=payload.world_id,
                part=next_part,
            )
            story_embed = build_story_embed(world_id=payload.world_id, part=next_part)
            status_embed = build_status_embed(player=updated_player, world_id=payload.world_id)

            await interaction.response.send_message(
                embeds=[story_embed, status_embed],
                view=next_view,
                ephemeral=False,
            )
            await _metric(self.services, "story.choice.applied")

        except Exception:
            logger.exception("error in persistent_story_view callback")
            if interaction.response.is_done():
                await interaction.followup.send("❌ حدث خطأ أثناء تنفيذ الاختيار.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ حدث خطأ أثناء تنفيذ الاختيار.", ephemeral=True)
