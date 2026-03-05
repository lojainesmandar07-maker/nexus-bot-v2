from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol


logger = logging.getLogger("nexus.discord.progression_service")


# ============================================================
# Ports required by this service
# ============================================================

class StoryServicePort(Protocol):
    async def get_part(self, world_id: str, part_id: str) -> Optional[dict]:
        ...

    async def get_ending(self, world_id: str, ending_id: str) -> Optional[dict]:
        ...


class PlayerPolicyPort(Protocol):
    """
    Optional extra policy hooks (anti-cheat / moderation / rate limits).
    Keep optional so progression remains standalone.
    """
    async def validate_choice_allowed(
        self,
        *,
        user_id: int,
        world_id: str,
        part_id: str,
        choice_id: str,
    ) -> tuple[bool, Optional[str]]:
        ...


# ============================================================
# Domain config
# ============================================================

WORLD_UNLOCK_RULES: dict[str, dict[str, Any]] = {
    "fantasy": {"required_level": 1, "required_world_ending": None},
    "retro": {"required_level": 3, "required_world_ending": "fantasy"},
    "future": {"required_level": 5, "required_world_ending": "retro"},
    "alternate": {"required_level": 7, "required_world_ending": "future"},
}


@dataclass(frozen=True)
class ProgressionResult:
    updated_player: dict[str, Any]
    next_part_id: str
    applied_effects: dict[str, Any]


@dataclass
class ProgressionService:
    """
    Production progression engine:
    - consistent world unlock logic
    - safe choice application
    - adaptive trait accumulation
    - anti-grind xp dampening
    - ending rewards integration
    """

    story: StoryServicePort
    policy: Optional[PlayerPolicyPort] = None
    max_corruption: int = 100
    min_corruption: int = 0
    max_stat_value: int = 1_000_000

    # anti-grind config
    anti_grind_enabled: bool = True
    repeat_window_size: int = 10
    repeat_penalty_floor: float = 0.35   # minimum 35% reward
    repeat_penalty_step: float = 0.10    # -10% per repetition over threshold
    repeat_threshold: int = 2            # no penalty for first 2 repeats

    # XP curve config
    level_cap: int = 100
    base_xp_for_next_level: int = 100
    xp_growth: float = 1.35

    # optional event audit
    include_audit_metadata: bool = True

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------

    async def can_access_world(self, player: dict, world_id: str) -> tuple[bool, str]:
        world_id = self._normalize_world_id(world_id)
        rules = WORLD_UNLOCK_RULES.get(world_id)
        if not rules:
            return False, "World does not exist."

        level = int(player.get("level", 1))
        required_level = int(rules["required_level"])
        if level < required_level:
            return False, f"Need level {required_level}. Your level is {level}."

        required_ending_world = rules.get("required_world_ending")
        if required_ending_world:
            endings = player.get("endings", {}) or {}
            if not endings.get(required_ending_world):
                return False, f"Complete {required_ending_world} ending first."

        return True, "Unlocked"

    async def apply_choice(
        self,
        *,
        player: dict,
        world_id: str,
        current_part: dict,
        choice_id: str,
    ) -> tuple[dict, str, dict]:
        """
        Returns:
            (updated_player, next_part_id, applied_effects)
        """
        world_id = self._normalize_world_id(world_id)
        user_id = int(player.get("user_id", 0))
        part_id = str(current_part.get("id", "unknown"))

        # Optional policy hook
        if self.policy:
            allowed, reason = await self.policy.validate_choice_allowed(
                user_id=user_id,
                world_id=world_id,
                part_id=part_id,
                choice_id=choice_id,
            )
            if not allowed:
                raise ValueError(reason or "Choice blocked by policy.")

        choices = current_part.get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"Part '{part_id}' has no selectable choices.")

        selected = None
        for c in choices:
            if str(c.get("id")) == str(choice_id):
                selected = c
                break

        if not selected:
            raise ValueError(f"Choice '{choice_id}' not found in part '{part_id}'.")

        next_part_id = self._resolve_next_part_id(selected)
        if not next_part_id:
            raise ValueError(f"Choice '{choice_id}' does not define next part.")

        # Validate target node exists as part or ending
        next_part = await self.story.get_part(world_id, next_part_id)
        next_ending = await self.story.get_ending(world_id, next_part_id)
        if not next_part and not next_ending:
            raise ValueError(
                f"Broken content link: {world_id}:{part_id}:{choice_id} -> {next_part_id}"
            )

        # Clone/normalize player object
        p = self._normalize_player(player)

        # Apply base choice effects
        raw_effects = selected.get("effects", {}) or {}
        if not isinstance(raw_effects, dict):
            raise ValueError("Choice effects must be an object.")

        # Anti-grind scalar (applies to xp/shards/gold-like stats)
        scalar = self._compute_anti_grind_scalar(p, world_id, part_id, choice_id)

        applied_effects: dict[str, Any] = {}
        for key, val in raw_effects.items():
            if isinstance(val, (int, float)):
                adjusted = self._adjust_effect_with_scalar(key, float(val), scalar)
                self._apply_numeric_effect(p, key, adjusted)
                applied_effects[key] = adjusted
            else:
                # Non-numeric effect paths can be extended (flags/tags)
                applied_effects[key] = val

        # Adaptive narrative traits
        trait_weights = selected.get("trait_weights", {}) or {}
        if isinstance(trait_weights, dict):
            traits = p.setdefault("traits", {})
            for t_key, t_val in trait_weights.items():
                if not isinstance(t_val, (int, float)):
                    continue
                traits[t_key] = int(traits.get(t_key, 0)) + int(t_val)
            applied_effects["trait_weights"] = trait_weights

        # Set world/progress
        p["current_world"] = world_id
        world_progress = p.setdefault("world_progress", {})
        world_progress[world_id] = next_part_id

        # Apply ending rewards if next is ending
        if next_ending:
            self._apply_ending_rewards(p, world_id, next_ending, applied_effects)

        # Update anti-grind history
        self._record_recent_choice(p, world_id, part_id, choice_id)

        # Recompute level from XP
        self._recompute_level(p)

        # Optional audit metadata
        if self.include_audit_metadata:
            audit = p.setdefault("_audit", {})
            audit["last_choice_at"] = datetime.now(timezone.utc).isoformat()
            audit["last_choice"] = {
                "world_id": world_id,
                "part_id": part_id,
                "choice_id": choice_id,
                "next_part_id": next_part_id,
                "scalar": scalar,
            }

        return p, next_part_id, applied_effects

    # ---------------------------------------------------------
    # Internal helpers: unlock/effects/player normalization
    # ---------------------------------------------------------

    def _normalize_world_id(self, world_id: str) -> str:
        aliases = {"past": "retro", "alt": "alternate"}
        return aliases.get(world_id, world_id)

    def _normalize_player(self, player: dict) -> dict:
        p = dict(player)

        p.setdefault("level", 1)
        p.setdefault("xp", 0)
        p.setdefault("shards", 0)
        p.setdefault("gold", 0)
        p.setdefault("corruption", 0)
        p.setdefault("current_world", "fantasy")
        p.setdefault("traits", {})

        p.setdefault("endings", {
            "fantasy": None,
            "retro": None,
            "future": None,
            "alternate": None,
        })

        p.setdefault("world_progress", {
            "fantasy": None,
            "retro": None,
            "future": None,
            "alternate": None,
        })

        p.setdefault("_recent_choices", [])
        return p

    def _resolve_next_part_id(self, choice: dict) -> Optional[str]:
        nxt = choice.get("next_part_id", choice.get("next"))
        if nxt is None:
            return None
        return str(nxt)

    def _adjust_effect_with_scalar(self, key: str, value: float, scalar: float) -> int:
        """
        Apply anti-grind scalar only to farmable rewards.
        Keep narrative-sensitive stats unscaled by default.
        """
        farmable = {"xp", "shards", "gold", "coins"}
        if key in farmable:
            return int(round(value * scalar))
        return int(round(value))

    def _apply_numeric_effect(self, player: dict, key: str, delta: int) -> None:
        cur = player.get(key, 0)
        if not isinstance(cur, (int, float)):
            # if key exists but non-numeric, replace safely
            cur = 0
        new_value = int(cur) + int(delta)

        if key == "corruption":
            new_value = max(self.min_corruption, min(self.max_corruption, new_value))
        else:
            new_value = max(-self.max_stat_value, min(self.max_stat_value, new_value))

        player[key] = new_value

    def _apply_ending_rewards(
        self,
        player: dict,
        world_id: str,
        ending: dict,
        applied_effects: dict[str, Any],
    ) -> None:
        # mark ending completion
        ending_type = str(ending.get("type", "normal"))
        endings = player.setdefault("endings", {})
        # store ending id if available, else type
        ending_id = ending.get("id")
        endings[world_id] = str(ending_id or ending_type)

        rewards = ending.get("rewards", {}) or {}
        if isinstance(rewards, dict):
            for r_key, r_val in rewards.items():
                if isinstance(r_val, (int, float)):
                    self._apply_numeric_effect(player, r_key, int(r_val))
                    applied_effects[f"ending_reward:{r_key}"] = int(r_val)
                else:
                    # items/titles handled by inventory/achievement services later
                    applied_effects[f"ending_reward:{r_key}"] = r_val

        # optional direct next_world hint
        next_world = ending.get("next_world")
        if isinstance(next_world, str):
            applied_effects["next_world_hint"] = next_world

    # ---------------------------------------------------------
    # Anti-grind
    # ---------------------------------------------------------

    def _compute_anti_grind_scalar(
        self,
        player: dict,
        world_id: str,
        part_id: str,
        choice_id: str,
    ) -> float:
        if not self.anti_grind_enabled:
            return 1.0

        recent = player.get("_recent_choices", [])
        if not isinstance(recent, list):
            return 1.0

        target = f"{world_id}:{part_id}:{choice_id}"
        repeats = 0
        for item in recent[-self.repeat_window_size:]:
            if item == target:
                repeats += 1

        # first N repeats no penalty
        over = max(0, repeats - self.repeat_threshold)
        penalty = over * self.repeat_penalty_step
        scalar = 1.0 - penalty
        if scalar < self.repeat_penalty_floor:
            scalar = self.repeat_penalty_floor
        return scalar

    def _record_recent_choice(
        self,
        player: dict,
        world_id: str,
        part_id: str,
        choice_id: str,
    ) -> None:
        target = f"{world_id}:{part_id}:{choice_id}"
        recent = player.setdefault("_recent_choices", [])
        recent.append(target)

        # hard cap history size for payload control
        max_size = max(self.repeat_window_size * 5, 50)
        if len(recent) > max_size:
            del recent[:-max_size]

    # ---------------------------------------------------------
    # Level progression
    # ---------------------------------------------------------

    def _recompute_level(self, player: dict) -> None:
        xp = int(player.get("xp", 0))
        current_level = int(player.get("level", 1))

        if xp < 0:
            xp = 0
            player["xp"] = 0

        new_level = current_level
        while new_level < self.level_cap:
            needed = self._xp_for_level(new_level + 1)
            if xp >= needed:
                new_level += 1
            else:
                break

        if new_level != current_level:
            player["level"] = new_level
            logger.info(
                "Player %s leveled up: %s -> %s",
                player.get("user_id"),
                current_level,
                new_level,
            )

    def _xp_for_level(self, level: int) -> int:
        # total xp threshold to reach "level"
        # exponential-ish curve
        if level <= 1:
            return 0
        return int(self.base_xp_for_next_level * (self.xp_growth ** (level - 2)))
