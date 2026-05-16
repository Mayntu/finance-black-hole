import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.config import settings
from core.database import async_session_factory
from models.expense import Expense
from models.goal import Goal
from models.user import User
from services.ai_service import ai_service
from services.gamification import ACHIEVEMENTS, gamification_service
from models.achievement import Achievement
from tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _generate_insights() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).options(selectinload(User.achievements))
        )
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
                if len(expenses) < 3:
                    continue

                goal_result = await session.execute(
                    select(Goal).where(
                        Goal.user_id == user.id,
                        Goal.is_active == True,
                        Goal.is_completed == False,
                    ).limit(1)
                )
                goal = goal_result.scalar_one_or_none()

                summary_lines = [f"Траты за неделю ({len(expenses)} записей):"]
                cat_totals: dict[str, float] = {}
                for e in expenses:
                    cat_totals[e.category] = cat_totals.get(e.category, 0) + e.amount
                for cat, total in sorted(cat_totals.items(), key=lambda x: -x[1]):
                    summary_lines.append(f"- {cat}: {total:,.0f} ₸")

                insight = await ai_service.generate_weekly_insight(
                    expenses_summary="\n".join(summary_lines),
                    goal=goal.title if goal else "не задана",
                    deadline=str(goal.deadline) if goal and goal.deadline else "без дедлайна",
                    days_remaining=(goal.deadline - __import__("datetime").date.today()).days
                    if goal and goal.deadline
                    else 0,
                )

                await bot.send_message(
                    user.telegram_id,
                    f"🧠 *Еженедельный инсайт*\n\n{insight.text}",
                    parse_mode="Markdown",
                )

                # Grant "insight_received" achievement on first insight
                existing = {a.code for a in user.achievements}
                if "insight_received" not in existing:
                    ach = Achievement(user_id=user.id, code="insight_received")
                    session.add(ach)
                    await session.commit()
                    info = ACHIEVEMENTS["insight_received"]
                    await bot.send_message(
                        user.telegram_id,
                        f"🏅 *Новое достижение!*\n{info['emoji']} {info['title']} — {info['desc']}",
                        parse_mode="Markdown",
                    )

            except Exception as e:
                logger.error("insight_failed", user_id=user.id, error=str(e))

    await bot.session.close()


@celery_app.task(name="tasks.weekly_insight.send_weekly_insights")
def send_weekly_insights() -> None:
    asyncio.run(_generate_insights())
