from datetime import date, datetime, timedelta, timezone
from typing import Any

import calendar
import structlog
from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.expense import Expense
from models.goal import Goal
from models.user import User

logger = structlog.get_logger(__name__)

_MONTH_GENITIVE = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _ru_days_word(n: int) -> str:
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return f"{n} дней"
    if n % 10 == 1:
        return f"{n} день"
    if n % 10 in (2, 3, 4):
        return f"{n} дня"
    return f"{n} дней"


def _month_spend_projection(
    *,
    spent: float,
    monthly_budget: float | None,
    now: datetime,
) -> dict[str, Any]:
    """Linear extrapolation of month spend to calendar month-end (UTC)."""
    y, m = now.year, now.month
    dim = calendar.monthrange(y, m)[1]
    month_start_d = date(y, m, 1)
    month_end_d = date(y, m, dim)
    today_d = now.date()
    elapsed = (today_d - month_start_d).days + 1
    elapsed = max(1, min(elapsed, dim))
    days_remaining = max(0, (month_end_d - today_d).days)

    projected = round(float(spent) * (dim / elapsed), 2)
    month_title = f"{_MONTH_GENITIVE[m]} {y}"

    if days_remaining <= 0:
        tail = "сегодня последний день месяца"
    elif days_remaining == 1:
        tail = "остался 1 день до конца месяца"
    else:
        tail = f"осталось {_ru_days_word(days_remaining)} до конца месяца"

    out: dict[str, Any] = {
        "projected_end": projected,
        "month_title": month_title,
        "days_remaining": days_remaining,
        "calendar_tail": tail,
        "days_in_month": dim,
        "elapsed_days": elapsed,
    }

    if monthly_budget and monthly_budget > 0:
        b = float(monthly_budget)
        out["budget"] = round(b, 2)
        diff = projected - b
        if diff > 0.5:
            out["is_over"] = True
            out["over_amount"] = round(diff, 2)
        elif diff < -0.5:
            out["is_under"] = True
            out["under_amount"] = round(-diff, 2)
        else:
            out["is_on_track"] = True

    return out


def _safe_to_spend_block(
    *,
    budget: float,
    spent_month: float,
    days_remaining: int,
    today_spent: float,
) -> dict[str, Any]:
    """
    PocketGuard-style: (budget - spent_month) / days_remaining - today_spent.
    Last calendar day: daily allowance = remaining month budget (no division by 0).
    """
    b = float(budget)
    spent = float(spent_month)
    left_total = b - spent
    dr = max(0, int(days_remaining))
    ts = float(today_spent)

    if dr > 0:
        daily = left_total / dr
    else:
        daily = left_total

    safe = daily - ts

    return {
        "amount": round(safe, 2),
        "daily_allowance": round(daily, 2),
        "budget": round(b, 2),
        "spent_month": round(spent, 2),
        "days_remaining": dr,
        "today_spent": round(ts, 2),
    }


class AnalyticsService:

    # ── Category breakdown ─────────────────────────────────────────────────────

    async def get_category_breakdown(
        self, session: AsyncSession, user_id: int, days: int = 30
    ) -> list[dict]:
        """Returns categories sorted by total spend, with % of total."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = await session.execute(
            select(
                Expense.category,
                func.sum(Expense.amount).label("total"),
                func.count(Expense.id).label("count"),
            )
            .where(Expense.user_id == user_id, Expense.created_at >= since)
            .group_by(Expense.category)
            .order_by(func.sum(Expense.amount).desc())
        )
        rows = result.all()
        grand_total = sum(r.total for r in rows) or 1
        return [
            {
                "category": r.category,
                "total": round(r.total, 2),
                "count": r.count,
                "pct": round(r.total / grand_total * 100, 1),
            }
            for r in rows
        ]

    # ── Daily spending ─────────────────────────────────────────────────────────

    async def get_daily_spending(
        self, session: AsyncSession, user_id: int, days: int = 30
    ) -> list[dict]:
        """Returns per-day totals for the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = await session.execute(
            select(
                func.date(Expense.created_at).label("date"),
                func.sum(Expense.amount).label("total"),
                func.count(Expense.id).label("count"),
            )
            .where(Expense.user_id == user_id, Expense.created_at >= since)
            .group_by(func.date(Expense.created_at))
            .order_by(func.date(Expense.created_at))
        )
        return [
            {"date": str(r.date), "total": round(r.total, 2), "count": r.count}
            for r in result.all()
        ]

    # ── Consciousness stats ────────────────────────────────────────────────────

    async def get_consciousness_stats(
        self, session: AsyncSession, user_id: int, days: int = 30
    ) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = await session.execute(
            select(
                Expense.is_conscious,
                func.count(Expense.id).label("count"),
                func.sum(Expense.amount).label("total"),
            )
            .where(Expense.user_id == user_id, Expense.created_at >= since)
            .group_by(Expense.is_conscious)
        )
        rows = {r.is_conscious: {"count": r.count, "total": round(r.total, 2)} for r in result.all()}
        conscious = rows.get(True, {"count": 0, "total": 0})
        impulsive = rows.get(False, {"count": 0, "total": 0})
        total_count = conscious["count"] + impulsive["count"]
        return {
            "conscious": conscious,
            "impulsive": impulsive,
            "total_count": total_count,
            "conscious_pct": round(conscious["count"] / total_count * 100) if total_count else 0,
        }

    # ── Monthly summary ────────────────────────────────────────────────────────

    async def get_monthly_summary(
        self,
        session: AsyncSession,
        user_id: int,
        monthly_budget: float | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_end = month_start
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

        async def _sum(since, until=None):
            q = select(func.sum(Expense.amount)).where(
                Expense.user_id == user_id, Expense.created_at >= since
            )
            if until:
                q = q.where(Expense.created_at < until)
            r = await session.execute(q)
            return r.scalar_one_or_none() or 0.0

        current_month = await _sum(month_start)
        prev_month = await _sum(prev_month_start, prev_month_end)

        week_start = now - timedelta(days=7)
        current_week = await _sum(week_start)

        # Active goal
        goal_result = await session.execute(
            select(Goal)
            .where(Goal.user_id == user_id, Goal.is_active == True, Goal.is_completed == False)
            .order_by(Goal.created_at.desc())
            .limit(1)
        )
        goal = goal_result.scalar_one_or_none()

        projection = _month_spend_projection(
            spent=current_month,
            monthly_budget=monthly_budget,
            now=now,
        )

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_spent = await _sum(today_start)

        safe_to_spend = None
        if monthly_budget and float(monthly_budget) > 0:
            safe_to_spend = _safe_to_spend_block(
                budget=float(monthly_budget),
                spent_month=current_month,
                days_remaining=int(projection["days_remaining"]),
                today_spent=today_spent,
            )

        return {
            "current_month": round(current_month, 2),
            "prev_month": round(prev_month, 2),
            "month_change_pct": round((current_month - prev_month) / prev_month * 100, 1) if prev_month else None,
            "current_week": round(current_week, 2),
            "projection": projection,
            "safe_to_spend": safe_to_spend,
            "active_goal": {
                "id": goal.id,
                "title": goal.title,
                "target_amount": goal.target_amount,
                "current_amount": goal.current_amount,
                "pct": round(goal.current_amount / goal.target_amount * 100, 1) if goal.target_amount else 0,
                "deadline": str(goal.deadline) if goal and goal.deadline else None,
            } if goal else None,
        }

    # ── Personality context ────────────────────────────────────────────────────

    async def build_personality_context(
        self, session: AsyncSession, user_id: int
    ) -> str:
        """Build a text summary of last 30 days for AI personality evaluation."""
        breakdown = await self.get_category_breakdown(session, user_id, days=30)
        consciousness = await self.get_consciousness_stats(session, user_id, days=30)

        # Weekly patterns (last 4 weeks, day-of-week breakdown)
        since = datetime.now(timezone.utc) - timedelta(days=28)
        result = await session.execute(
            select(
                func.extract("dow", Expense.created_at).label("dow"),
                func.sum(Expense.amount).label("total"),
            )
            .where(Expense.user_id == user_id, Expense.created_at >= since)
            .group_by(func.extract("dow", Expense.created_at))
            .order_by(func.extract("dow", Expense.created_at))
        )
        dow_data = {int(r.dow): round(r.total, 0) for r in result.all()}
        dow_names = {0: "Вс", 1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб"}

        lines = [
            f"Анализ трат за 30 дней ({consciousness['total_count']} записей):",
            "",
            "По категориям:",
        ]
        for cat in breakdown[:8]:
            lines.append(f"  - {cat['category']}: {cat['total']:,.0f} ₸ ({cat['pct']}%, {cat['count']} раз)")

        lines.append("")
        lines.append(f"Осознанные: {consciousness['conscious_pct']}%")
        lines.append(
            f"Осознанных трат: {consciousness['conscious']['count']} "
            f"({consciousness['conscious']['total']:,.0f} ₸)"
        )
        lines.append(
            f"Импульсивных: {consciousness['impulsive']['count']} "
            f"({consciousness['impulsive']['total']:,.0f} ₸)"
        )

        if dow_data:
            lines.append("")
            lines.append("Траты по дням недели:")
            for dow, total in sorted(dow_data.items()):
                lines.append(f"  {dow_names.get(dow, str(dow))}: {total:,.0f} ₸")

        return "\n".join(lines)

    # ── Should update personality ──────────────────────────────────────────────

    def should_update_personality(self, user: User, expense_count: int) -> bool:
        if expense_count < 20:
            return False
        if user.last_personality_update is None:
            return True
        days_since = (datetime.now(timezone.utc) - user.last_personality_update).days
        return days_since >= 14


analytics_service = AnalyticsService()
