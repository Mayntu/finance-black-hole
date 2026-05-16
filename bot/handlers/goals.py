from datetime import date, datetime, timezone

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import main_menu_keyboard
from bot.states import GoalStates
from core.auth import create_dashboard_token
from core.config import settings
from models.goal import Goal
from models.user import User
from services.gamification import format_progress_bar

logger = structlog.get_logger(__name__)
router = Router()


def _goals_web_keyboard(telegram_id: int) -> InlineKeyboardMarkup | None:
    base = (settings.WEBAPP_URL or settings.WEBHOOK_URL or "").rstrip("/")
    if not base.startswith("https://"):
        return None
    token = create_dashboard_token(telegram_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🌐 Цели в приложении",
            web_app=WebAppInfo(url=f"{base}/goals?token={token}"),
        ),
    ]])


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


@router.message(Command("goals"))
@router.message(F.text == "🎯 Цели")
async def cmd_goals(message: Message, session: AsyncSession) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user.id, Goal.is_active == True)
        .order_by(Goal.created_at.desc())
    )
    goals = result.scalars().all()

    if not goals:
        await message.answer(
            "У тебя нет активных целей.\n\n"
            "Добавь цель командой /goal — и каждая трата будет оцениваться относительно неё. 🎯",
            reply_markup=_goals_web_keyboard(message.from_user.id),
        )
        return

    lines = ["🎯 *Твои цели:*\n"]
    for g in goals:
        pct = min(100, int(g.current_amount / g.target_amount * 100)) if g.target_amount > 0 else 0
        bar = format_progress_bar(pct)
        lines.append(f"*{g.title}*")
        lines.append(f"{bar}  {pct}%")
        lines.append(f"💰 {g.current_amount:,.0f} / {g.target_amount:,.0f} ₸")
        if g.deadline:
            days_left = (g.deadline - date.today()).days
            if days_left > 0:
                needed_per_day = (g.target_amount - g.current_amount) / days_left
                lines.append(f"📅 {days_left} дней • нужно {needed_per_day:,.0f} ₸/день")
            else:
                lines.append("⏰ Дедлайн прошёл!")
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=_goals_web_keyboard(message.from_user.id))


@router.message(Command("goal"))
async def cmd_new_goal(message: Message, state: FSMContext) -> None:
    await message.answer(
        "🎯 Создаём новую цель!\n\nКак называется твоя цель?\nНапример: *MacBook Pro*, *Отпуск в Турции*, *Финансовая подушка*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(GoalStates.waiting_title)


@router.message(GoalStates.waiting_title)
async def goal_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await message.answer("Какая сумма нужна? (введи число в тенге):")
    await state.set_state(GoalStates.waiting_amount)


@router.message(GoalStates.waiting_amount)
async def goal_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(" ", "").replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введи положительную сумму (например: 180000):")
        return

    await state.update_data(amount=amount)
    await message.answer(
        "До какой даты хочешь накопить?\n"
        "Введи дату в формате *ДД.ММ.ГГГГ* — или напиши *нет* чтобы пропустить:",
        parse_mode="Markdown",
    )
    await state.set_state(GoalStates.waiting_deadline)


_SKIP_WORDS = {"нет", "skip", "пропустить", "пропускаю", "не знаю", "без", "-", "no", "позже"}


@router.message(GoalStates.waiting_deadline)
async def goal_deadline(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    deadline = None

    if text.lower().rstrip("!.,") not in _SKIP_WORDS:
        try:
            deadline = datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            await message.answer("Неверный формат 🤔 Введи дату как *ДД.ММ.ГГГГ* или напиши *нет*:", parse_mode="Markdown")
            return

    data = await state.get_data()
    user = await _get_user(session, message.from_user.id)

    goal = Goal(
        user_id=user.id,
        title=data["title"],
        target_amount=data["amount"],
        deadline=deadline,
    )
    session.add(goal)
    await state.clear()

    deadline_text = f"📅 Дедлайн: {deadline.strftime('%d.%m.%Y')}" if deadline else "📅 Без дедлайна"
    if deadline:
        days_left = (deadline - date.today()).days
        needed = data["amount"] / max(days_left, 1)
        deadline_text += f"\n💡 Нужно откладывать ~{needed:,.0f} ₸/день"

    await message.answer(
        f"✅ *Цель создана!*\n\n"
        f"🎯 {data['title']}\n"
        f"💰 {data['amount']:,.0f} ₸\n"
        f"{deadline_text}\n\n"
        "Теперь каждая трата будет показывать % от этой цели. Это меняет восприятие каждого расхода 💡",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
