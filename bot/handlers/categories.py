import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.states import CategoryStates
from models.user import User

logger = structlog.get_logger(__name__)
router = Router()

DEFAULT_CATEGORIES = [
    "Еда", "Транспорт", "Развлечения", "Здоровье",
    "Одежда", "Коммунальные", "Подписки", "Прочее",
]


def _categories_keyboard(custom: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for cat in custom:
        rows.append([
            InlineKeyboardButton(text=f"🗑 {cat}", callback_data=f"delcat:{cat[:20]}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить категорию", callback_data="addcat")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_text(custom: list[str]) -> str:
    default_line = "  •  ".join(DEFAULT_CATEGORIES)
    if custom:
        custom_line = "\n".join(f"  • {c}" for c in custom)
        return (
            "📂 *Твои категории*\n\n"
            f"*Кастомные* (AI использует в первую очередь):\n{custom_line}\n\n"
            f"*Стандартные:* {default_line}\n\n"
            "Нажми 🗑 рядом с категорией чтобы удалить, или добавь новую ↓"
        )
    return (
        "📂 *Категории трат*\n\n"
        "Кастомных категорий нет.\n\n"
        f"*Стандартные:* {default_line}\n\n"
        "Добавь свои — AI будет использовать их при распознавании трат ↓"
    )


@router.message(Command("categories"))
@router.message(F.text == "📂 Категории")
async def cmd_categories(message: Message, session: AsyncSession) -> None:
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer("Сначала /start 👋")
        return

    custom = user.custom_categories or []
    await message.answer(
        _build_text(custom),
        parse_mode="Markdown",
        reply_markup=_categories_keyboard(custom),
    )


@router.callback_query(F.data == "addcat")
async def cb_addcat(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.answer(
        "Напиши название новой категории:\n"
        "_Например: Спортзал, Такси, Рестораны, Инвестиции_",
        parse_mode="Markdown",
    )
    await state.set_state(CategoryStates.waiting_new_category)
    await call.answer()


@router.message(CategoryStates.waiting_new_category)
async def handle_new_category(message: Message, state: FSMContext, session: AsyncSession) -> None:
    name = message.text.strip()[:30]
    if not name:
        await message.answer("Пустое название не подойдёт. Попробуй ещё раз:")
        return

    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not user:
        await state.clear()
        return

    custom = list(user.custom_categories or [])
    if name in custom:
        await message.answer(f"Категория *{name}* уже есть.", parse_mode="Markdown")
        await state.clear()
        return
    if len(custom) >= 10:
        await message.answer("Максимум 10 кастомных категорий. Удали одну чтобы добавить новую.")
        await state.clear()
        return

    custom.append(name)
    user.custom_categories = custom
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ Добавил категорию *{name}*",
        parse_mode="Markdown",
        reply_markup=_categories_keyboard(custom),
    )
    logger.info("category_added", user_id=user.id, name=name)


@router.callback_query(F.data.startswith("delcat:"))
async def cb_delcat(call: CallbackQuery, session: AsyncSession) -> None:
    name = call.data.split(":", 1)[1]
    result = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
    user = result.scalar_one_or_none()
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    custom = list(user.custom_categories or [])
    # Match by prefix since we truncated to 20 chars in callback_data
    matched = next((c for c in custom if c.startswith(name) or c[:20] == name), None)
    if matched:
        custom.remove(matched)
        user.custom_categories = custom
        await session.commit()
        await call.message.edit_text(
            _build_text(custom),
            parse_mode="Markdown",
            reply_markup=_categories_keyboard(custom),
        )
        await call.answer(f"Удалил «{matched}»")
    else:
        await call.answer("Категория не найдена", show_alert=True)
