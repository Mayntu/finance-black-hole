"""Server-side rendered web dashboard (Jinja2 templates)."""
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import get_db
from core.auth import decode_dashboard_token
from models.expense import Expense
from models.goal import Goal
from models.mission import Mission
from models.user import User
from services.analytics import analytics_service
from services.gamification import ACHIEVEMENTS, get_level_info, format_progress_bar

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="web/templates")

# Register custom Jinja2 filters
templates.env.filters["abs"] = abs


# ── Auth helper ────────────────────────────────────────────────────────────────

async def _get_user_from_token(
    token: str,
    session: AsyncSession,
) -> User | None:
    telegram_id = decode_dashboard_token(token)
    if not telegram_id:
        return None
    result = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.achievements))
    )
    return result.scalar_one_or_none()


def _redirect_auth_error(token: str) -> RedirectResponse:
    return RedirectResponse(url=f"/error?msg=Ссылка+истекла+или+недействительна&token={token}", status_code=302)


def _bootstrap_response(request: Request, next_path: str):
    """Mini App menu opens /dashboard without ?token= — show loader + initData auth."""
    return templates.TemplateResponse(
        "webapp_bootstrap.html",
        {"request": request, "next_path": next_path},
    )


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def web_dashboard(
    request: Request,
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
):
    if not token:
        return _bootstrap_response(request, "/dashboard")

    user = await _get_user_from_token(token, session)
    if not user:
        return _redirect_auth_error(token)

    lvl = get_level_info(user.xp)
    monthly = await analytics_service.get_monthly_summary(session, user.id, user.monthly_budget)
    categories = await analytics_service.get_category_breakdown(session, user.id, days=30)
    daily = await analytics_service.get_daily_spending(session, user.id, days=30)

    # Recent 5 expenses
    recent_result = await session.execute(
        select(Expense)
        .where(Expense.user_id == user.id)
        .order_by(Expense.created_at.desc())
        .limit(5)
    )
    recent = recent_result.scalars().all()

    # Active mission
    now = datetime.now(timezone.utc)
    mission_result = await session.execute(
        select(Mission).where(
            Mission.user_id == user.id,
            Mission.is_completed == False,
            Mission.expires_at > now,
        ).limit(1)
    )
    mission = mission_result.scalar_one_or_none()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "token": token,
        "user": user,
        "lvl": lvl,
        "monthly": monthly,
        "categories_json": json.dumps(categories),
        "daily_json": json.dumps(daily),
        "recent": recent,
        "mission": mission,
        "xp_bar": format_progress_bar(lvl["progress_pct"], 12),
        "now": now,
    })


# ── Goals ──────────────────────────────────────────────────────────────────────

@router.get("/goals", response_class=HTMLResponse)
async def web_goals(
    request: Request,
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
):
    if not token:
        return _bootstrap_response(request, "/goals")

    user = await _get_user_from_token(token, session)
    if not user:
        return _redirect_auth_error(token)

    result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user.id)
        .order_by(Goal.is_active.desc(), Goal.created_at.desc())
    )
    goals = result.scalars().all()

    from datetime import date as date_type
    today = date_type.today()

    goals_data = []
    for g in goals:
        pct = min(100, round(g.current_amount / g.target_amount * 100, 1)) if g.target_amount else 0
        days_left = (g.deadline - today).days if g.deadline else None
        daily_needed = None
        if days_left and days_left > 0 and not g.is_completed:
            remaining = max(0, g.target_amount - g.current_amount)
            daily_needed = round(remaining / days_left)
        goals_data.append({
            "obj": g,
            "pct": pct,
            "days_left": days_left,
            "daily_needed": daily_needed,
        })

    active = [g for g in goals_data if g["obj"].is_active and not g["obj"].is_completed]
    completed = [g for g in goals_data if g["obj"].is_completed]

    return templates.TemplateResponse("goals.html", {
        "request": request,
        "token": token,
        "user": user,
        "lvl": get_level_info(user.xp),
        "active_goals": active,
        "completed_goals": completed,
    })


# ── History ────────────────────────────────────────────────────────────────────

@router.get("/history", response_class=HTMLResponse)
async def web_history(
    request: Request,
    token: str | None = Query(None),
    page: int = Query(1, ge=1),
    category: str = Query(""),
    conscious: str = Query(""),
    session: AsyncSession = Depends(get_db),
):
    if not token:
        return _bootstrap_response(request, "/history")

    user = await _get_user_from_token(token, session)
    if not user:
        return _redirect_auth_error(token)

    limit = 25
    q = select(Expense).where(Expense.user_id == user.id)
    if category:
        q = q.where(Expense.category == category)
    if conscious == "true":
        q = q.where(Expense.is_conscious == True)
    elif conscious == "false":
        q = q.where(Expense.is_conscious == False)

    count_result = await session.execute(
        select(func.count()).select_from(q.subquery())
    )
    total = count_result.scalar_one()
    pages = max(1, (total + limit - 1) // limit)

    result = await session.execute(
        q.order_by(Expense.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    expenses = result.scalars().all()

    # All categories for filter
    cats_result = await session.execute(
        select(Expense.category).where(Expense.user_id == user.id).distinct()
    )
    all_cats = [r[0] for r in cats_result.all()]

    return templates.TemplateResponse("history.html", {
        "request": request,
        "token": token,
        "user": user,
        "lvl": get_level_info(user.xp),
        "expenses": expenses,
        "total": total,
        "page": page,
        "pages": pages,
        "all_cats": sorted(all_cats),
        "filter_category": category,
        "filter_conscious": conscious,
    })


# ── Profile ────────────────────────────────────────────────────────────────────

@router.get("/profile", response_class=HTMLResponse)
async def web_profile(
    request: Request,
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
):
    if not token:
        return _bootstrap_response(request, "/profile")

    user = await _get_user_from_token(token, session)
    if not user:
        return _redirect_auth_error(token)

    lvl = get_level_info(user.xp)
    consciousness = await analytics_service.get_consciousness_stats(session, user.id, days=30)
    monthly = await analytics_service.get_monthly_summary(session, user.id, user.monthly_budget)

    earned_codes = {a.code for a in user.achievements}
    achievements = [
        {
            "code": code,
            "earned": code in earned_codes,
            **info,
        }
        for code, info in ACHIEVEMENTS.items()
    ]

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "token": token,
        "user": user,
        "lvl": lvl,
        "xp_bar": format_progress_bar(lvl["progress_pct"], 16),
        "achievements": achievements,
        "earned_count": len(earned_codes),
        "total_ach": len(ACHIEVEMENTS),
        "consciousness": consciousness,
        "monthly": monthly,
    })


# ── Error page ─────────────────────────────────────────────────────────────────

@router.get("/error", response_class=HTMLResponse)
async def web_error(request: Request, msg: str = "Произошла ошибка"):
    return templates.TemplateResponse("error.html", {"request": request, "msg": msg})
