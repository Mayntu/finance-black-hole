"""
Celery task: batch personality recalculation for all eligible users.
Runs every Sunday night — supplements the inline trigger in the bot.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select

from core.config import settings
from core.database import async_session_factory
from models.achievement import Achievement
from models.user import User
from services.ai_service import ai_service
from services.analytics import analytics_service
from services.gamification import ACHIEVEMENTS
from tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _run_personality_updates() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)

    async with async_session_factory() as session:
        # Users with 20+ expenses that haven't been evaluated recently
        from sqlalchemy import func
        from models.expense import Expense

        eligible_result = await session.execute(
            select(User)
            .join(Expense, Expense.user_id == User.id)
            .group_by(User.id)
            .having(func.count(Expense.id) >= 20)
            .where(
                (User.last_personality_update == None)
                | (User.last_personality_update <= cutoff)
            )
        )
        users = eligible_result.scalars().all()
        logger.info("personality_batch_start", eligible=len(users))

        for user in users:
            try:
                context = await analytics_service.build_personality_context(session, user.id)
                personality = await ai_service.evaluate_financial_personality(context)

                old = user.financial_personality
                user.financial_personality = personality.archetype
                user.last_personality_update = datetime.now(timezone.utc)
                user.personality_data = {
                    "emoji": personality.emoji,
                    "description": personality.description,
                    "dominant_pattern": personality.dominant_pattern,
                    "growth_hint": personality.growth_hint,
                }
                session.add(user)

                if old and old != personality.archetype:
                    # Notify user of archetype change
                    existing = await session.execute(
                        select(Achievement).where(
                            Achievement.user_id == user.id,
                            Achievement.code == "personality_up",
                        )
                    )
                    if not existing.scalar_one_or_none():
                        session.add(Achievement(user_id=user.id, code="personality_up"))

                    ach_info = ACHIEVEMENTS["personality_up"]
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            f"🧬 *Твоя финансовая личность изменилась!*\n\n"
                            f"{personality.emoji} *{old}* → *{personality.archetype}*\n\n"
                            f"{personality.description}\n\n"
                            f"💡 _{personality.growth_hint}_\n\n"
                            f"{ach_info['emoji']} Ачивка: _{ach_info['title']}_",
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.warning("notify_personality_failed", user_id=user.id, error=str(e))

                await session.commit()
                logger.info("personality_updated", user_id=user.id, archetype=personality.archetype)

            except Exception as e:
                logger.error("personality_update_error", user_id=user.id, error=str(e))
                await session.rollback()

    await bot.session.close()


@celery_app.task(name="tasks.personality_update.run_personality_updates")
def run_personality_updates() -> None:
    asyncio.run(_run_personality_updates())
