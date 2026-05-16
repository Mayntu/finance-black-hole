from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.deps import get_db
from models.goal import Goal
from models.user import User

router = APIRouter(prefix="/api/goals", tags=["goals"])


@router.get("/{telegram_id}")
async def list_goals(
    telegram_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user.id)
        .order_by(Goal.is_active.desc(), Goal.created_at.desc())
    )
    goals = result.scalars().all()

    def _goal_dict(g: Goal) -> dict:
        from datetime import date
        pct = round(g.current_amount / g.target_amount * 100, 1) if g.target_amount else 0
        days_left = None
        daily_needed = None
        if g.deadline and not g.is_completed:
            days_left = (g.deadline - date.today()).days
            remaining = max(0, g.target_amount - g.current_amount)
            daily_needed = round(remaining / max(days_left, 1), 0) if days_left > 0 else None

        return {
            "id": g.id,
            "title": g.title,
            "target_amount": g.target_amount,
            "current_amount": g.current_amount,
            "pct": pct,
            "is_active": g.is_active,
            "is_completed": g.is_completed,
            "deadline": str(g.deadline) if g.deadline else None,
            "days_left": days_left,
            "daily_needed": daily_needed,
            "created_at": g.created_at.isoformat(),
            "completed_at": g.completed_at.isoformat() if g.completed_at else None,
        }

    active = [_goal_dict(g) for g in goals if g.is_active and not g.is_completed]
    completed = [_goal_dict(g) for g in goals if g.is_completed]

    return {
        "active": active,
        "completed": completed,
        "total": len(goals),
    }
