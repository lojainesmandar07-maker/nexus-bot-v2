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
# Protocols (ports) expected from your app services container
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
        returns (updated_player, next_part_id, applied_effects)
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
# Constants
# ============================================================

WORLD_ORDER = ["fantasy", "retro", "future", "alternate"]
WORLD_NAMES = {
    "fantasy": "Fantasy",
    "retro": "Retro",
    "future": "Future",
    "alternate": "Alternate",
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
# Button Payload
# ============================================================

@dataclass(frozen=True)
class StoryButtonPayload:
    """
    Stable custom_id schema for persistent buttons.
    Keep short but deterministic:
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
# Embed Builders
# ============================================================

def build_story_embed(*, world_id: str, part: dict) -> discord.Embed:
    color = WORLD_COLORS.get(world_id, WORLD_COLORS["general"])
    title = part.get("title", "Untitled Part")
    text = part.get("text", "No text provided.")
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
            c_text = c.get("text", f"Choice {idx}")
            lines.append(f"{idx}. {c_text}")
        embed.add_field(name="🧭 Choices", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="🏁 Ending", value="This node has no choices.", inline=False)

    embed.set_footer(text=f"{WORLD_NAMES.get(world_id, world_id)} • {part_id}")
    return embed


def build_status_embed(*, player: dict, world_id: str) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Your Status",
        color=WORLD_COLORS["general"],
        timestamp=datetime.now(timezone.utc),
    )

    level = player.get("level", 1)
    xp = player.get("xp", 0)
    shards = player.get("shards", 0)
    corruption = player.get("corruption", 0)
    current_world = player.get("current_world", "fantasy")

    embed.add_field(name="Level", value=str(level), inline=True)
    embed.add_field(name="XP", value=str(xp), inline=True)
    embed.add_field(name="Shards", value=str(shards), inline=True)
    embed.add_field(name="Corruption", value=str(corruption), inline=True)
    embed.add_field(name="Current World", value=f"{WORLD_EMOJIS.get(current_world, '🌍')} {WORLD_NAMES.get(current_world, current_world)}", inline=True)

    traits = player.get("traits", {})
    if isinstance(traits, dict) and traits:
        trait_lines = [f"• {k}: {v}" for k, v in traits.items()]
        embed.add_field(name="Adaptive Traits", value="\n".join(trait_lines)[:1024], inline=False)

    endings = player.get("endings", {})
    if isinstance(endings, dict):
        ending_lines = []
        for w in WORLD_ORDER:
            ending_lines.append(f"{WORLD_EMOJIS[w]} {WORLD_NAMES[w]}: {endings.get(w) or '-'}")
        embed.add_field(name="Endings", value="\n".join(ending_lines)[:1024], inline=False)

    return embed


# ============================================================
# Persistent Story View
# ============================================================

class PersistentStoryView(discord.ui.View):
    """
    timeout=None => persistent across runtime.
    For full reboot recovery, re-create views from session states on startup.
    """

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
        self.world_id = world_id
        self.part = part
        self.part_id = part.get("id", "unknown")

        choices = part.get("choices", [])
        for c in choices:
            choice_id = str(c.get("id", "unknown"))
            label = str(c.get("text", "choice"))[:80]
            payload = new_payload(user_id, world_id, self.part_id, choice_id)

            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=payload.encode()[:100],  # Discord limit safety
            )
            button.callback = self._on_choice_pressed  # type: ignore[assignment]
            self.add_item(button)

    async def _on_choice_pressed(self, interaction: discord.Interaction) -> None:
        try:
            custom_id = str((interaction.data or {}).get("custom_id", ""))
            payload = StoryButtonPayload.decode(custom_id)
            if not payload:
                await interaction.response.send_message("❌ Invalid button payload.", ephemeral=True)
                return

            # user lock
            if interaction.user.id != payload.user_id:
                await interaction.response.send_message("❌ This button belongs to another player.", ephemeral=True)
                return

            # safety: if part in payload doesn't exist anymore -> no dead button
            current_part = await self.services.story.get_part(payload.world_id, payload.part_id)
            if not current_part:
                await interaction.response.send_message(
                    "⚠️ This story node is no longer available. Use `/continue` to recover.",
                    ephemeral=True,
                )
                await self.services.metrics.inc("story.button.dead_node")
                return

            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)

            # apply choice via progression engine
            updated_player, next_part_id, effects = await self.services.progression.apply_choice(
                player=player,
                world_id=payload.world_id,
                current_part=current_part,
                choice_id=payload.choice_id,
            )

            # persist
            await self.services.players.update_player(interaction.user.id, updated_player)
            await self.services.sessions.set_current_session(
                interaction.user.id,
                world_id=payload.world_id,
                part_id=next_part_id,
            )

            choice_text = "unknown"
            for c in current_part.get("choices", []):
                if str(c.get("id")) == payload.choice_id:
                    choice_text = str(c.get("text", "unknown"))
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

            # resolve next node
            ending = await self.services.story.get_ending(payload.world_id, next_part_id)
            if ending:
                await self.services.metrics.inc("story.ending.reached")
                ending_embed = discord.Embed(
                    title=f"🏁 {ending.get('title', 'Ending')}",
                    description=str(ending.get("text", "No ending text.")),
                    color=WORLD_COLORS.get(payload.world_id, WORLD_COLORS["ok"]),
                    timestamp=datetime.now(timezone.utc),
                )
                ending_embed.add_field(
                    name="Type",
                    value=str(ending.get("type", "normal")),
                    inline=True,
                )
                rewards = ending.get("rewards", {})
                if rewards:
                    ending_embed.add_field(
                        name="Rewards",
                        value=f"`{rewards}`"[:1024],
                        inline=False,
                    )

                status = build_status_embed(player=updated_player, world_id=payload.world_id)
                await interaction.response.send_message(
                    embeds=[ending_embed, status],
                    ephemeral=False,
                )
                return

            next_part = await self.services.story.get_part(payload.world_id, next_part_id)
            if not next_part:
                # no dead button: graceful fallback
                await interaction.response.send_message(
                    "⚠️ Next node is missing. Your progress was saved. Use `/continue`.",
                    ephemeral=True,
                )
                await self.services.metrics.inc("story.button.missing_next")
                return

            next_view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
                world_id=payload.world_id,
                part=next_part,
            )
            story = build_story_embed(world_id=payload.world_id, part=next_part)
            status = build_status_embed(player=updated_player, world_id=payload.world_id)

            await interaction.response.send_message(
                embeds=[story, status],
                view=next_view,
                ephemeral=False,
            )
            await self.services.metrics.inc("story.choice.applied")

        except Exception:
            logger.exception("Error while handling story button click.")
            if interaction.response.is_done():
                await interaction.followup.send("❌ Unexpected error while processing choice.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Unexpected error while processing choice.", ephemeral=True)


# ============================================================
# Story Commands Cog
# ============================================================

class StoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot, services: ServicesPort) -> None:
        self.bot = bot
        self.services = services

    # --------------------------
    # /start
    # --------------------------
    @app_commands.command(name="start", description="Start a story world")
    @app_commands.describe(world="Choose a world to start")
    @app_commands.choices(world=[
        app_commands.Choice(name="🌲 Fantasy", value="fantasy"),
        app_commands.Choice(name="📜 Retro", value="retro"),
        app_commands.Choice(name="🤖 Future", value="future"),
        app_commands.Choice(name="🌀 Alternate", value="alternate"),
    ])
    async def start(
        self,
        interaction: discord.Interaction,
        world: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            target_world = world or "fantasy"
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)

            # unlock check
            can_access, reason = await self.services.progression.can_access_world(player, target_world)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await self.services.metrics.inc("story.start.locked")
                return

            # channel policy check
            allowed, policy_reason = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=target_world,
            )
            if not allowed:
                await interaction.followup.send(policy_reason or "Blocked by channel policy.", ephemeral=True)
                await self.services.metrics.inc("story.start.channel_blocked")
                return

            if policy_reason:
                await interaction.followup.send(policy_reason, ephemeral=True)

            # resolve start part from story files (NOT hardcoded)
            start_part_id = await self.services.story.get_start_part_id(target_world)
            start_part = await self.services.story.get_part(target_world, start_part_id)
            if not start_part:
                await interaction.followup.send(
                    f"❌ Start part `{start_part_id}` is missing for world `{target_world}`.",
                    ephemeral=True
                )
                await self.services.metrics.inc("story.start.missing_start_part")
                return

            # update session/player
            player["current_world"] = target_world
            world_progress = player.get("world_progress", {})
            world_progress[target_world] = start_part_id
            player["world_progress"] = world_progress

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

            story_embed = build_story_embed(world_id=target_world, part=start_part)
            status_embed = build_status_embed(player=player, world_id=target_world)

            await interaction.followup.send(
                embeds=[story_embed, status_embed],
                view=view,
                ephemeral=False,
            )
            await self.services.metrics.inc("story.start.success")

        except Exception:
            logger.exception("Failed to execute /start")
            await interaction.followup.send("❌ Failed to start story.", ephemeral=True)

    # --------------------------
    # /continue
    # --------------------------
    @app_commands.command(name="continue", description="Continue from your latest checkpoint")
    async def continue_story(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            current_world = player.get("current_world") or "fantasy"

            # channel policy check
            allowed, policy_reason = await self.services.policy.validate_usage(
                guild_id=interaction.guild.id if interaction.guild else None,
                channel_id=interaction.channel_id,
                world_id=current_world,
            )
            if not allowed:
                await interaction.followup.send(policy_reason or "Blocked by channel policy.", ephemeral=True)
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

            # verify unlock (if world changed due to stale data)
            can_access, reason = await self.services.progression.can_access_world(player, world_id)
            if not can_access:
                await interaction.followup.send(f"🔒 {reason}", ephemeral=True)
                await self.services.metrics.inc("story.continue.locked")
                return

            part = await self.services.story.get_part(world_id, part_id)
            if not part:
                # fallback to world start
                start_part_id = await self.services.story.get_start_part_id(world_id)
                part = await self.services.story.get_part(world_id, start_part_id)
                if not part:
                    await interaction.followup.send("❌ Unable to recover story checkpoint.", ephemeral=True)
                    await self.services.metrics.inc("story.continue.recovery_failed")
                    return

                part_id = start_part_id
                await self.services.sessions.set_current_session(interaction.user.id, world_id=world_id, part_id=part_id)

            view = PersistentStoryView(
                services=self.services,
                user_id=interaction.user.id,
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
            await self.services.metrics.inc("story.continue.success")

        except Exception:
            logger.exception("Failed to execute /continue")
            await interaction.followup.send("❌ Failed to continue story.", ephemeral=True)

    # --------------------------
    # /worlds
    # --------------------------
    @app_commands.command(name="worlds", description="Show your world unlock and progress status")
    async def worlds(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            embed = discord.Embed(
                title="🌍 Worlds Status",
                description="Unlock + progress snapshot",
                color=WORLD_COLORS["general"],
                timestamp=datetime.now(timezone.utc),
            )

            for world_id in WORLD_ORDER:
                can_access, reason = await self.services.progression.can_access_world(player, world_id)
                progress = (player.get("world_progress", {}) or {}).get(world_id) or "-"
                ending = (player.get("endings", {}) or {}).get(world_id) or "-"
                state = "✅ Unlocked" if can_access else f"🔒 {reason}"

                embed.add_field(
                    name=f"{WORLD_EMOJIS[world_id]} {WORLD_NAMES[world_id]}",
                    value=f"{state}\nPart: `{progress}`\nEnding: `{ending}`",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("story.worlds.opened")

        except Exception:
            logger.exception("Failed to execute /worlds")
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to load worlds.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to load worlds.", ephemeral=True)

    # --------------------------
    # /my_stats
    # --------------------------
    @app_commands.command(name="my_stats", description="Show your current player stats")
    async def my_stats(self, interaction: discord.Interaction) -> None:
        try:
            player = await self.services.players.get_or_create_player(interaction.user.id, interaction.user.name)
            world_id = player.get("current_world") or "fantasy"
            embed = build_status_embed(player=player, world_id=world_id)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.services.metrics.inc("story.stats.opened")
        except Exception:
            logger.exception("Failed to execute /my_stats")
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to load stats.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to load stats.", ephemeral=True)


# ============================================================
# Extension setup
# ============================================================

async def setup(bot: commands.Bot) -> None:
    """
    Requires bot.svc to exist and implement ServicesPort.
    """
    services = getattr(bot, "svc", None)
    if services is None:
        raise RuntimeError("Bot has no `svc` container attached.")
    await bot.add_cog(StoryCog(bot, services))
    logger.info("StoryCog loaded.")
