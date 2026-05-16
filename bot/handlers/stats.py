from datetime import datetime, timedelta, timezone

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.utils import extract_amount
from models.expense import Expense
from models.goal import Goal
from models.user import User
from services.ai_service import ai_service
from services.gamification import format_progress_bar

logger = structlog.get_logger(__name__)
router = Router()


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


# ── /today ─────────────────────────────────────────────────────────────────────

@router.message(Command("today"))
@router.message(F.text == "📊 Сегодня")
async def cmd_today(message: Message, session: AsyncSession) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(Expense)
        .where(Expense.user_id == user.id, Expense.created_at >= today_start)
        .order_by(Expense.created_at.desc())
    )
    expenses = result.scalars().all()

    if not expenses:
        hint = "\n\n_Напиши что потратил — кофе 300, такси 1200_" if not user.monthly_budget else ""
        await message.answer(f"Сегодня трат пока нет 🌱{hint}", parse_mode="Markdown")
        return

    total = sum(e.amount for e in expenses)
    lines = [f"📊 *Сегодня* — {total:,.0f} ₸\n"]

    for e in expenses[:10]:
        icon = "✅" if e.is_conscious else "⚠️" if e.is_conscious is False else "•"
        lines.append(f"{icon} {e.label} — {e.amount:,.0f} ₸")

    if len(expenses) > 10:
        lines.append(f"_...и ещё {len(expenses) - 10} трат_")

    if user.monthly_budget:
        daily_budget = user.monthly_budget / 30
        remaining = daily_budget - total
        lines.append("")
        if remaining >= 0:
            lines.append(f"💰 Дневной лимит: *{daily_budget:,.0f} ₸* • остаток *{remaining:,.0f} ₸*")
        else:
            lines.append(f"⚠️ Превышение дневного лимита на *{abs(remaining):,.0f} ₸*")

    # Monthly spending context
    month_start = today_start.replace(day=1)
    month_result = await session.execute(
        select(func.sum(Expense.amount))
        .where(Expense.user_id == user.id, Expense.created_at >= month_start)
    )
    month_total = month_result.scalar_one_or_none() or 0
    lines.append(f"\n📅 За месяц: {month_total:,.0f} ₸")

    if user.monthly_budget:
        budget_pct = min(100, int(month_total / user.monthly_budget * 100))
        bar = format_progress_bar(budget_pct, length=8)
        lines.append(f"{bar}  {budget_pct}% от бюджета")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /week ──────────────────────────────────────────────────────────────────────

@router.message(Command("week"))
@router.message(F.text == "📈 Неделя")
async def cmd_week(message: Message, session: AsyncSession) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    prev_week_start = week_start - timedelta(days=7)

    result = await session.execute(
        select(Expense.category, func.sum(Expense.amount).label("total"))
        .where(Expense.user_id == user.id, Expense.created_at >= week_start)
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    )
    this_week = result.all()

    if not this_week:
        await message.answer("За эту неделю трат нет 🌱\n\n_Начни записывать прямо сейчас!_", parse_mode="Markdown")
        return

    result_prev = await session.execute(
        select(func.sum(Expense.amount))
        .where(Expense.user_id == user.id, Expense.created_at >= prev_week_start, Expense.created_at < week_start)
    )
    prev_total = result_prev.scalar_one_or_none() or 0

    total = sum(row.total for row in this_week)

    diff_text = ""
    if prev_total > 0:
        diff_pct = (total - prev_total) / prev_total * 100
        arrow = "📈" if diff_pct > 0 else "📉"
        diff_text = f"  {arrow} {diff_pct:+.1f}% к прошлой"

    lines = [f"📈 *Неделя* — {total:,.0f} ₸{diff_text}\n"]

    lines.append("Топ категорий:")
    for row in this_week[:5]:
        pct = int(row.total / total * 100)
        bar_len = max(1, pct // 10)
        bar = "█" * bar_len
        lines.append(f"  {bar} {row.category}: {row.total:,.0f} ₸ ({pct}%)")

    # Active goal progress reminder
    goal_result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user.id, Goal.is_active == True, Goal.is_completed == False)
        .limit(1)
    )
    goal = goal_result.scalar_one_or_none()
    if goal and goal.target_amount > 0:
        pct = min(100, int(goal.current_amount / goal.target_amount * 100))
        lines.append(f"\n🎯 *{goal.title}*: {pct}% накоплено ({goal.current_amount:,.0f} / {goal.target_amount:,.0f} ₸)")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /insight ───────────────────────────────────────────────────────────────────

@router.message(Command("insight"))
@router.message(F.text == "🧠 Инсайт")
async def cmd_insight(message: Message, session: AsyncSession) -> None:
    """On-demand Financial Mirror — AI reflection based on last 7 days."""
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    exp_result = await session.execute(
        select(Expense)
        .where(Expense.user_id == user.id, Expense.created_at >= week_ago)
    )
    expenses = exp_result.scalars().all()

    if len(expenses) < 3:
        await message.answer(
            "🧠 *Финансовое зеркало*\n\n"
            "Нужно минимум 3 траты за последние 7 дней.\n\n"
            f"_Пока записей: {len(expenses)}. Продолжай вести учёт — и AI покажет твои паттерны!_",
            parse_mode="Markdown",
        )
        return

    thinking = await message.answer("🧠 _Анализирую твои траты..._", parse_mode="Markdown")

    goal_result = await session.execute(
        select(Goal).where(Goal.user_id == user.id, Goal.is_active == True, Goal.is_completed == False).limit(1)
    )
    goal = goal_result.scalar_one_or_none()

    cat_totals: dict[str, float] = {}
    for e in expenses:
        cat_totals[e.category] = cat_totals.get(e.category, 0) + e.amount

    top_sorted = sorted(expenses, key=lambda x: -x.amount)[:5]
    top_lines = []
    for e in top_sorted:
        lab = (e.label or e.raw_input or e.category or "?")[:50]
        top_lines.append(f"- {lab}: {e.amount:,.0f} ₸ ({e.category})")

    def _coffee_like(e: Expense) -> bool:
        t = f"{e.label or ''} {e.raw_input or ''}".lower()
        keys = ("кофе", "coffee", "латте", "капучино", "кафе", "cafe", "раф", "espresso", "эспрессо")
        return e.category == "Еда" and any(k in t for k in keys)

    coffee_exp = [e for e in expenses if _coffee_like(e)]
    coffee_n = len(coffee_exp)
    coffee_sum = sum(e.amount for e in coffee_exp)

    summary_lines = [f"Траты за 7 дней ({len(expenses)} записей), всего {sum(e.amount for e in expenses):,.0f} ₸:"]
    for cat, total in sorted(cat_totals.items(), key=lambda x: -x[1]):
        summary_lines.append(f"- {cat}: {total:,.0f} ₸")
    summary_lines.append("")
    summary_lines.append("Топ-5 отдельных трат (название — сумма — категория):")
    summary_lines.extend(top_lines)
    if coffee_n:
        summary_lines.append("")
        summary_lines.append(f"Кофе/кафе за 7 дней: {coffee_n} раз на сумму {coffee_sum:,.0f} ₸.")

    goal_ctx = ""
    if goal and goal.target_amount and goal.target_amount > 0:
        left = max(0, goal.target_amount - goal.current_amount)
        goal_ctx = (
            f"\nПрогресс цели «{goal.title}»: накоплено {goal.current_amount:,.0f} / "
            f"{goal.target_amount:,.0f} ₸, осталось {left:,.0f} ₸."
        )

    summary_lines.append(goal_ctx)

    try:
        import datetime as _dt
        insight = await ai_service.generate_weekly_insight(
            expenses_summary="\n".join(summary_lines),
            goal=goal.title if goal else "не задана",
            deadline=str(goal.deadline) if goal and goal.deadline else "без дедлайна",
            days_remaining=(goal.deadline - _dt.date.today()).days if goal and goal.deadline else 0,
        )
        await thinking.edit_text(
            f"🧠 *Финансовое зеркало*\n\n{insight.text}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("insight_failed", error=str(e))
        await thinking.edit_text("😔 Не удалось сгенерировать инсайт. Попробуй позже.")


# ── /budget ────────────────────────────────────────────────────────────────────

@router.message(Command("budget"))
@router.message(F.text == "💳 Бюджет")
async def cmd_budget(message: Message, session: AsyncSession) -> None:
    """/budget 150000 — set monthly spending limit."""
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        # Show current budget
        if user.monthly_budget:
            await message.answer(
                f"💰 Твой месячный бюджет: *{user.monthly_budget:,.0f} ₸*\n\n"
                f"Чтобы изменить: _/budget 200000_",
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                "Месячный бюджет не задан.\n\n"
                "Установи: _/budget 150000_\n"
                "После этого в /today будет показывать сколько осталось на день.",
                parse_mode="Markdown",
            )
        return

    amount = extract_amount(args[1])
    if not amount or amount < 1000:
        await message.answer("Введи сумму: _/budget 150000_", parse_mode="Markdown")
        return

    user.monthly_budget = amount
    session.add(user)

    daily = amount / 30
    await message.answer(
        f"✅ Месячный бюджет установлен: *{amount:,.0f} ₸*\n\n"
        f"Это примерно *{daily:,.0f} ₸/день*\n\n"
        "Теперь /today будет показывать сколько осталось.",
        parse_mode="Markdown",
    )
