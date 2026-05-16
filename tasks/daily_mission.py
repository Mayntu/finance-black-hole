import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select

from core.config import settings
from core.database import async_session_factory
from models.expense import Expense
from models.goal import Goal
from models.mission import Mission
from models.user import User
from services.ai_service import ai_service
from tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _generate_missions() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    async with async_session_factory() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                exp_result = await session.execute(
                    select(Expense).where(
                        Expense.user_id == user.id,
                        Expense.created_at >= week_ago,
                    )
                )
                expenses = exp_result.scalars().all()
                if len(expenses) < 5:
                    continue

                cat_totals: dict[str, float] = {}
                for e in expenses:
                    cat_totals[e.category] = cat_totals.get(e.category, 0) + e.amount
                weakness = max(cat_totals, key=cat_totals.get) if cat_totals else "Прочее"

                goal_result = await session.execute(
                    select(Goal).where(
                        Goal.user_id == user.id, Goal.is_active == True, Goal.is_completed == False
                    ).limit(1)
                )
                goal = goal_result.scalar_one_or_none()

                mission_data = await ai_service.generate_mission(
                    goal=goal.title if goal else "нет цели",
                    level=user.level,
                    weakness=weakness,
                )

                mission = Mission(
                    user_id=user.id,
                    title=mission_data.title,
                    description=mission_data.description,
                    success_criteria=mission_data.success_criteria,
                    why_this_mission=mission_data.why_this_mission,
                    reward_xp=mission_data.reward_xp,
                    difficulty=mission_data.difficulty,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
                session.add(mission)
                await session.flush()

                await bot.send_message(
                    user.telegram_id,
                    f"🎖 *Новая миссия!*\n\n"
                    f"*{mission_data.title}*\n"
                    f"{mission_data.description}\n\n"
                    f"✅ Условие: {mission_data.success_criteria}\n"
                    f"💡 {mission_data.why_this_mission}\n\n"
                    f"Награда: ⭐ {mission_data.reward_xp} XP • 7 дней",
                    parse_mode="Markdown",
                )
                await session.commit()

            except Exception as e:
                logger.error("mission_generation_failed", user_id=user.id, error=str(e))

    await bot.session.close()


@celery_app.task(name="tasks.daily_mission.generate_weekly_missions")
def generate_weekly_missions() -> None:
    asyncio.run(_generate_missions())
