from __future__ import annotations

from dataclasses import dataclass

from bot.registrar import CommandRegistrar
from bot.services.story_service import StoryService
from bot.services.progression_service import ProgressionService
from bot.services.channel_policy_service import ChannelPolicyService
from bot.services.session_service import SessionService
from bot.services.metrics_service import MetricsService
from bot.services.player_service import PlayerService


@dataclass(frozen=True)
class AppServices:
    story: StoryService
    progression: ProgressionService
    policy: ChannelPolicyService
    policy_admin: ChannelPolicyService
    sessions: SessionService
    players: PlayerService
    metrics: MetricsService
    registrar: CommandRegistrar


def build_services(content_root: str, policy_mode: str = "strict") -> AppServices:
    story = StoryService(content_root=__import__("pathlib").Path(content_root))
    policy = ChannelPolicyService(default_mode=policy_mode)
    sessions = SessionService()
    players = PlayerService()
    metrics = MetricsService()

    progression = ProgressionService(story=story)

    registrar = CommandRegistrar()

    return AppServices(
        story=story,
        progression=progression,
        policy=policy,
        policy_admin=policy,  # نفس الخدمة تستخدمها القصة + الإدارة
        sessions=sessions,
        players=players,
        metrics=metrics,
        registrar=registrar,
    )
