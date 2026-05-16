import asyncio
import structlog

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import MenuButtonWebApp, WebAppInfo, BotCommand

from bot.handlers import blackhole, categories, goals, help as help_handler, missions, profile, start, stats
from bot.middlewares import DbSessionMiddleware
from core.config import settings
from core.database import async_session_factory
from core.redis import close_redis, get_redis

logger = structlog.get_logger(__name__)


async def _setup_bot(bot: Bot) -> None:
    """Register commands and set the Mini App menu button."""
    # Commands visible in the /menu
    await bot.set_my_commands([
        BotCommand(command="start",      description="Начать / перезапустить"),
        BotCommand(command="help",       description="❓ Все команды и кнопки"),
        BotCommand(command="dashboard",  description="🌐 Открыть дашборд"),
        BotCommand(command="today",      description="📊 Статистика сегодня"),
        BotCommand(command="week",       description="📅 Статистика за неделю"),
        BotCommand(command="insight",    description="🧠 Финансовое зеркало AI"),
        BotCommand(command="goals",     description="🎯 Список целей"),
        BotCommand(command="save",       description="💰 Пополнить цель"),
        BotCommand(command="missions",   description="⚡ Мои миссии"),
        BotCommand(command="categories", description="📂 Мои категории"),
        BotCommand(command="budget",     description="💳 Бюджет"),
        BotCommand(command="profile",    description="👤 Профиль и достижения"),
    ])

    # Persistent Menu Button → opens Mini App directly (requires HTTPS URL)
    webapp_base = settings.WEBAPP_URL or settings.WEBHOOK_URL
    if webapp_base and webapp_base.startswith("https://"):
        base = webapp_base.rstrip("/")
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="📊 Дашборд",
                web_app=WebAppInfo(url=f"{base}/dashboard"),
            )
        )
        logger.info("menu_button_set", url=f"{base}/dashboard")
    else:
        logger.info("menu_button_skipped", reason="no https webapp_url configured")


async def main() -> None:
    redis = get_redis()
    storage = RedisStorage(redis=redis)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher(storage=storage)

    dp.update.middleware(DbSessionMiddleware(async_session_factory))

    # Routers (order matters — blackhole catches all free text, must be last)
    dp.include_router(start.router)
    dp.include_router(help_handler.router)
    dp.include_router(goals.router)
    dp.include_router(missions.router)
    dp.include_router(profile.router)
    dp.include_router(stats.router)
    dp.include_router(categories.router)
    dp.include_router(blackhole.router)

    await _setup_bot(bot)

    logger.info("bot_starting", mode="polling")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
