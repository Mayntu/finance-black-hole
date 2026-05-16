import asyncio
from datetime import date

import structlog
from aiogram import Bot
from sqlalchemy import select

from core.config import settings
from core.database import async_session_factory
from models.user import User
from tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _send_reminders() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    today = date.today()

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(
                (User.last_active_date != today) | (User.last_active_date.is_(None))
            )
        )
        users = result.scalars().all()

        for user in users:
            try:
                streak_text = (
                    f"🔥 Стрик {user.streak_days} дней под угрозой! Внеси любую трату чтобы сохранить его."
                    if user.streak_days > 0
                    else "Не забудь внести траты сегодня! Начни стрик 🔥"
                )
                await bot.send_message(user.telegram_id, streak_text)
            except Exception as e:
                logger.warning("reminder_failed", user_id=user.id, error=str(e))

    await bot.session.close()


@celery_app.task(name="tasks.reminders.send_daily_reminders")
def send_daily_reminders() -> None:
    asyncio.run(_send_reminders())
