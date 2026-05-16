from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth import get_current_user
from api.deps import get_db
from models.expense import Expense
from models.mission import Mission
from models.user import User
from services.analytics import analytics_service
from services.gamification import ACHIEVEMENTS, get_level_info

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/{telegram_id}")
async def get_dashboard(
    telegram_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Full dashboard data for the web frontend."""
    lvl_info = get_level_info(user.xp)
    monthly = await analytics_service.get_monthly_summary(session, user.id, user.monthly_budget)
    category_breakdown = await analytics_service.get_category_breakdown(session, user.id, days=30)
    daily_spending = await analytics_service.get_daily_spending(session, user.id, days=30)

    # Recent expenses
    result = await session.execute(
        select(Expense)
        .where(Expense.user_id == user.id)
        .order_by(Expense.created_at.desc())
        .limit(5)
    )
    recent = result.scalars().all()

    # Active mission
    now = datetime.now(timezone.utc)
    mission_result = await session.execute(
        select(Mission).where(
            Mission.user_id == user.id,
            Mission.is_completed == False,
            Mission.expires_at > now,
        ).order_by(Mission.created_at.desc()).limit(1)
    )
    mission = mission_result.scalar_one_or_none()

    # Achievements
    result2 = await session.execute(
        select(User).where(User.id == user.id).options(selectinload(User.achievements))
    )
    user_with_ach = result2.scalar_one()
    earned_codes = {a.code for a in user_with_ach.achievements}
    achievements = [
        {
            "code": code,
            "earned": code in earned_codes,
            **info,
        }
        for code, info in ACHIEVEMENTS.items()
    ]

    return {
        "user": {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "full_name": user.full_name,
            "currency": user.currency,
            "level": lvl_info["level"],
            "level_name": lvl_info["name"],
            "level_emoji": lvl_info["emoji"],
            "xp": user.xp,
            "xp_progress_pct": lvl_info["progress_pct"],
            "next_level": lvl_info["next_level"],
            "streak_days": user.streak_days,
            "financial_personality": user.financial_personality,
            "personality_data": user.personality_data,
            "monthly_budget": user.monthly_budget,
        },
        "stats": monthly,
        "category_breakdown": category_breakdown,
        "daily_spending": daily_spending,
        "recent_expenses": [
            {
                "id": e.id,
                "label": e.label,
                "amount": e.amount,
                "category": e.category,
                "is_conscious": e.is_conscious,
                "created_at": e.created_at.isoformat(),
            }
            for e in recent
        ],
        "active_mission": {
            "id": mission.id,
            "title": mission.title,
            "description": mission.description,
            "success_criteria": mission.success_criteria,
            "reward_xp": mission.reward_xp,
            "difficulty": mission.difficulty,
            "expires_at": mission.expires_at.isoformat(),
        } if mission else None,
        "achievements": achievements,
    }
