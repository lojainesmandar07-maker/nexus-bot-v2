import asyncio
import logging
import os

from bot.app import create_bot


def setup_logging() -> None:
    level = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def _main() -> int:
    setup_logging()
    logger = logging.getLogger("discord-bot.main")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN is missing.")
        return 1

    bot = create_bot()

    try:
        await bot.start(token)
        return 0
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130
    except Exception:
        logger.exception("Unexpected startup failure.")
        return 1
    finally:
        await bot.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
