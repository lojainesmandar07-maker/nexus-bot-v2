from dataclasses import dataclass, field
from typing import Any


# ---- Minimal in-memory adapters for now (Phase A/B transition) ----

@dataclass
class PlayerStore:
    players: dict[int, dict[str, Any]] = field(default_factory=dict)

    def get_or_create(self, user_id: int, username: str) -> dict[str, Any]:
        if user_id not in self.players:
            self.players[user_id] = {
                "user_id": user_id,
                "username": username,
                "level": 1,
                "xp": 0,
                "shards": 0,
                "corruption": 0,
                "current_world": "fantasy",
                "world_progress": {
                    "fantasy": "FANTASY_001",
                    "retro": None,
                    "future": None,
                    "alternate": None,
                },
                "endings": {
                    "fantasy": None,
                    "retro": None,
                    "future": None,
                    "alternate": None,
                },
                "traits": {"brave": 0, "greedy": 0, "diplomatic": 0, "chaotic": 0},
            }
        return self.players[user_id]

    def save(self, user: dict[str, Any]) -> None:
        self.players[user["user_id"]] = user


@dataclass
class ChannelPolicyStore:
    # guild_id -> world_id -> channel_id
    mappings: dict[int, dict[str, int]] = field(default_factory=dict)

    def set_world_channel(self, guild_id: int, world_id: str, channel_id: int) -> None:
        self.mappings.setdefault(guild_id, {})[world_id] = channel_id

    def get_world_channel(self, guild_id: int, world_id: str) -> int | None:
        return self.mappings.get(guild_id, {}).get(world_id)


@dataclass
class StoryStore:
    # Replace with content-engine bundle soon
    parts: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "FANTASY_001": {
            "id": "FANTASY_001",
            "world_id": "fantasy",
            "title": "Awakening Under Violet Skies",
            "text": "You awaken in ruins. A glowing gate hums. A masked scout watches silently.",
            "choices": [
                {"id": "c1", "text": "Approach the gate", "next_part_id": "FANTASY_002",
                 "effects": {"xp": 5, "corruption": 1}, "trait_weights": {"brave": 2}},
                {"id": "c2", "text": "Talk to the scout", "next_part_id": "FANTASY_003",
                 "effects": {"xp": 4, "shards": 1}, "trait_weights": {"diplomatic": 2}},
            ],
        },
        "FANTASY_002": {
            "id": "FANTASY_002",
            "world_id": "fantasy",
            "title": "The First Rift Echo",
            "text": "The rift repeats your thoughts. It feels alive and dangerous.",
            "choices": [
                {"id": "c1", "text": "Step inside", "next_part_id": "FANTASY_004",
                 "effects": {"xp": 8}, "trait_weights": {"chaotic": 2}},
                {"id": "c2", "text": "Seal it", "next_part_id": "FANTASY_004",
                 "effects": {"xp": 6}, "trait_weights": {"diplomatic": 1}},
            ],
        },
        "FANTASY_003": {
            "id": "FANTASY_003",
            "world_id": "fantasy",
            "title": "The Scout’s Warning",
            "text": "The scout says your choices are remembered by the world itself.",
            "choices": [
                {"id": "c1", "text": "Trust the scout", "next_part_id": "FANTASY_004",
                 "effects": {"xp": 6}, "trait_weights": {"diplomatic": 1}},
                {"id": "c2", "text": "Ignore and run", "next_part_id": "FANTASY_004",
                 "effects": {"xp": 5, "corruption": 1}, "trait_weights": {"chaotic": 1}},
            ],
        },
        "FANTASY_004": {
            "id": "FANTASY_004",
            "world_id": "fantasy",
            "title": "Branch of Fate",
            "text": "A fork appears: light path and shadow path. End of demo arc.",
            "choices": [
                {"id": "c1", "text": "Take light path", "next_part_id": "FANTASY_END_LIGHT",
                 "effects": {"xp": 10}, "trait_weights": {"brave": 1}},
                {"id": "c2", "text": "Take shadow path", "next_part_id": "FANTASY_END_DARK",
                 "effects": {"xp": 10, "corruption": 2}, "trait_weights": {"greedy": 1}},
            ],
        },
        "FANTASY_END_LIGHT": {
            "id": "FANTASY_END_LIGHT",
            "world_id": "fantasy",
            "title": "Ending: Keeper of Dawn",
            "text": "You preserved hope and stabilized the first rift.",
            "choices": [],
            "ending": "light"
        },
        "FANTASY_END_DARK": {
            "id": "FANTASY_END_DARK",
            "world_id": "fantasy",
            "title": "Ending: Whisper Crown",
            "text": "You embraced forbidden whispers and bent the rift to your will.",
            "choices": [],
            "ending": "dark"
        },
    })

    def get_part(self, part_id: str) -> dict[str, Any] | None:
        return self.parts.get(part_id)


@dataclass
class UnlockService:
    def can_access_world(self, player: dict[str, Any], world_id: str) -> tuple[bool, str]:
        level = int(player.get("level", 1))
        endings = player.get("endings", {})
        rules = {
            "fantasy": {"required_level": 1, "required_ending": None},
            "retro": {"required_level": 3, "required_ending": "fantasy"},
            "future": {"required_level": 5, "required_ending": "retro"},
            "alternate": {"required_level": 7, "required_ending": "future"},
        }
        if world_id not in rules:
            return False, "Unknown world."

        req = rules[world_id]
        if level < req["required_level"]:
            return False, f"Need level {req['required_level']} (you are {level})."

        if req["required_ending"] and not endings.get(req["required_ending"]):
            return False, f"Complete {req['required_ending']} ending first."

        return True, "Unlocked."


@dataclass
class StoryEngine:
    story_store: StoryStore

    def apply_choice(self, player: dict[str, Any], current_part: dict[str, Any], choice_id: str) -> str:
        choice = next((c for c in current_part.get("choices", []) if c["id"] == choice_id), None)
        if not choice:
            raise ValueError(f"Invalid choice: {choice_id}")

        for key, value in choice.get("effects", {}).items():
            if key in player and isinstance(player[key], int):
                player[key] += int(value)

        traits = player.get("traits", {})
        for t, w in choice.get("trait_weights", {}).items():
            traits[t] = int(traits.get(t, 0)) + int(w)
        player["traits"] = traits

        next_part_id = choice["next_part_id"]
        world_id = current_part["world_id"]
        player["current_world"] = world_id
        player["world_progress"][world_id] = next_part_id

        next_part = self.story_store.get_part(next_part_id)
        if next_part and next_part.get("ending"):
            player["endings"][world_id] = next_part["ending"]

        return next_part_id


@dataclass
class Services:
    players: PlayerStore = field(default_factory=PlayerStore)
    channels: ChannelPolicyStore = field(default_factory=ChannelPolicyStore)
    stories: StoryStore = field(default_factory=StoryStore)
    unlock: UnlockService = field(default_factory=UnlockService)
    engine: StoryEngine = field(init=False)

    def __post_init__(self) -> None:
        self.engine = StoryEngine(self.stories)
