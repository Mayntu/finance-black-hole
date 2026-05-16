import structlog
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import main_menu_keyboard
from bot.states import OnboardingStates
from bot.utils import clean_title, extract_amount, is_skip
from models.goal import Goal
from models.user import User

logger = structlog.get_logger(__name__)
router = Router()


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    existing = result.scalar_one_or_none()

    if existing:
        streak_word = "день" if existing.streak_days == 1 else "дня" if 2 <= existing.streak_days <= 4 else "дней"
        await message.answer(
            f"С возвращением, {existing.full_name}! 👋\n\n"
            f"🔥 Стрик: {existing.streak_days} {streak_word}  •  Уровень {existing.level}\n\n"
            "Что потратил?",
            reply_markup=main_menu_keyboard(),
        )
        return

    # New user — take name from Telegram automatically
    name = message.from_user.first_name or "друг"
    await state.update_data(full_name=name)

    await message.answer(
        f"Привет, {name}! Я *FinanceBlackHole* 🕳️\n\n"
        "Просто пиши что потратил — _кофе 300_, _такси 1200_, _продукты 3500_ — "
        "и я сам разберу сумму и категорию. Никаких форм.\n\n"
        "Есть финансовая цель прямо сейчас? Например:\n"
        "_«накопить на MacBook 450к»_\n"
        "_«iPhone за 180 000»_\n"
        "_«отпуск в Дубае 300 тыс»_\n\n"
        "Напиши цель или просто *нет* чтобы пропустить:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(OnboardingStates.waiting_goal)


# ── Goal step ──────────────────────────────────────────────────────────────────

@router.message(OnboardingStates.waiting_goal)
async def onboarding_goal(message: Message, state: FSMContext) -> None:
    text = message.text.strip()

    if is_skip(text):
        await state.update_data(goal_title=None, goal_amount=None)
        await _ask_categories(message, state)
        return

    # Try to extract amount inline: "накопить на MacBook 450к"
    amount = extract_amount(text)
    title = clean_title(text) if amount else text

    await state.update_data(goal_title=title)

    if amount:
        await state.update_data(goal_amount=amount)
        await _ask_categories(message, state)
    else:
        await message.answer(
            f"Отлично — *{title}* 🎯\n\nНа какую сумму? Можно написать: _450000_, _450к_, _1.5млн_\n\n"
            "Или *нет* если не знаешь пока:",
            parse_mode="Markdown",
        )
        await state.set_state(OnboardingStates.waiting_goal_amount)


@router.message(OnboardingStates.waiting_goal_amount)
async def onboarding_goal_amount(message: Message, state: FSMContext) -> None:
    text = message.text.strip()

    if is_skip(text):
        await state.update_data(goal_amount=None)
        await _ask_categories(message, state)
        return

    amount = extract_amount(text)
    if not amount:
        await message.answer(
            "Не смог распознать сумму 🤔\n"
            "Напиши число: _450000_, _450к_, _1.5млн_ — или *нет* чтобы пропустить:",
            parse_mode="Markdown",
        )
        return

    await state.update_data(goal_amount=amount)
    await _ask_categories(message, state)


# ── Categories step ────────────────────────────────────────────────────────────

async def _ask_categories(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Последний шаг — свои категории трат.\n\n"
        "Напиши через запятую что важно именно тебе:\n"
        "_Спортзал, Такси, Рестораны, Инвестиции_\n\n"
        "Или *нет* — будем использовать стандартные (Еда, Транспорт, Развлечения…):",
        parse_mode="Markdown",
    )
    await state.set_state(OnboardingStates.waiting_categories)


@router.message(OnboardingStates.waiting_categories)
async def onboarding_categories(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    custom_categories: list[str] = []

    if not is_skip(text):
        custom_categories = [c.strip() for c in text.split(",") if c.strip()][:8]

    data = await state.get_data()
    name = data["full_name"]
    goal_title = data.get("goal_title")
    goal_amount = data.get("goal_amount")

    user = User(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=name,
        custom_categories=custom_categories,
    )
    session.add(user)
    await session.flush()

    if goal_title and goal_amount:
        goal = Goal(user_id=user.id, title=goal_title, target_amount=goal_amount)
        session.add(goal)

    await state.clear()

    goal_line = f"🎯 Цель: *{goal_title}* — {goal_amount:,.0f} ₸" if goal_title and goal_amount else "Цель: пока не задана (можно добавить позже — /goal)"
    cats_line = "📂 " + ", ".join(custom_categories) if custom_categories else "📂 Стандартные категории"

    await message.answer(
        f"Готово, {name}! 🚀\n\n"
        f"{goal_line}\n"
        f"{cats_line}\n\n"
        "Теперь просто пиши что потратил — в любой форме.\n"
        "_Попробуй прямо сейчас: кофе 300_",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )

    logger.info("user_registered", telegram_id=message.from_user.id, name=name)
