from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    channel_policy_mode: str
    guild_id: int | None

    @staticmethod
    def from_env() -> "Settings":
        guild_raw = os.getenv("BOT_GUILD_ID", "").strip()
        guild_id = int(guild_raw) if guild_raw.isdigit() else None
        return Settings(
            channel_policy_mode=os.getenv("CHANNEL_POLICY_MODE", "strict").lower(),
            guild_id=guild_id,
        )
