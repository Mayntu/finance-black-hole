from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.deps import get_db
from models.user import User
from services.analytics import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/{telegram_id}")
async def get_analytics(
    telegram_id: int,
    days: int = Query(30, ge=7, le=365, description="Period in days"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Aggregated spending analytics for charts and insights."""
    breakdown = await analytics_service.get_category_breakdown(session, user.id, days=days)
    daily = await analytics_service.get_daily_spending(session, user.id, days=days)
    consciousness = await analytics_service.get_consciousness_stats(session, user.id, days=days)
    monthly = await analytics_service.get_monthly_summary(session, user.id, user.monthly_budget)

    return {
        "period_days": days,
        "category_breakdown": breakdown,
        "daily_spending": daily,
        "consciousness": consciousness,
        "monthly_summary": monthly,
        "financial_personality": {
            "archetype": user.financial_personality,
            "data": user.personality_data,
        },
    }
