import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.auth import create_dashboard_token
from core.config import settings
from models.user import User
from services.gamification import ACHIEVEMENTS, format_progress_bar, get_level_info

logger = structlog.get_logger(__name__)
router = Router()


def _webapp_url(token: str, page: str = "dashboard") -> str:
    base = settings.WEBAPP_URL or settings.WEBHOOK_URL or "http://localhost:8000"
    return f"{base.rstrip('/')}/{page}?token={token}"


def _dashboard_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚀 Открыть дашборд",
            web_app=WebAppInfo(url=_webapp_url(token, "dashboard")),
        )
    ]])


@router.message(Command("profile"))
@router.message(F.text == "👤 Профиль")
async def cmd_profile(message: Message, session: AsyncSession) -> None:
    result = await session.execute(
        select(User)
        .where(User.telegram_id == message.from_user.id)
        .options(selectinload(User.achievements))
    )
    user = result.scalar_one_or_none()

    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    info = get_level_info(user.xp)
    bar = format_progress_bar(info["progress_pct"])
    streak_word = "день" if user.streak_days == 1 else "дня" if 2 <= user.streak_days <= 4 else "дней"

    personality = user.financial_personality or "не определена"
    personality_emoji = ""
    if user.personality_data:
        personality_emoji = user.personality_data.get("emoji", "") + " "

    header = (
        f"{personality_emoji}*{personality}* — уровень {info['level']} {info['emoji']}\n"
        f"_{info['name']}_"
    )

    if info["next_level"]:
        xp_line = f"⭐ XP: {user.xp} / {info['next_level']['xp_required']} до *{info['next_level']['name']}*"
    else:
        xp_line = f"⭐ XP: {user.xp} — максимальный уровень"

    personality_block = ""
    if user.personality_data:
        desc = user.personality_data.get("description", "")
        dominant = user.personality_data.get("dominant_pattern", "")
        hint = user.personality_data.get("growth_hint", "")
        if desc:
            personality_block = f"\n{desc}"
        if dominant:
            personality_block += f"\n\n💡 *Паттерн:* _{dominant}_"
        if hint:
            personality_block += f"\n📈 *Рост:* _{hint}_"

    earned = {a.code for a in user.achievements}
    ach_parts = []
    for code, ach_info in ACHIEVEMENTS.items():
        if code in earned:
            ach_parts.append(f"{ach_info['emoji']} {ach_info['title']}")
        else:
            ach_parts.append(f"🔒 {ach_info['title']}")

    ach_rows = []
    for i in range(0, len(ach_parts), 3):
        ach_rows.append("  ".join(ach_parts[i:i+3]))
    ach_text = "\n".join(ach_rows)

    text = (
        f"{header}\n\n"
        f"🔥 Стрик: *{user.streak_days}* {streak_word}\n"
        f"{xp_line}\n"
        f"{bar}  {info['progress_pct']}%"
        f"{personality_block}\n\n"
        f"🏆 *Достижения ({len(earned)}/{len(ACHIEVEMENTS)}):*\n"
        f"{ach_text}"
    )

    token = create_dashboard_token(user.telegram_id)
    await message.answer(text, parse_mode="Markdown", reply_markup=_dashboard_keyboard(token))


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message, session: AsyncSession) -> None:
    result = await session.execute(
        select(User).where(User.telegram_id == message.from_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    token = create_dashboard_token(user.telegram_id)
    await message.answer(
        "🌐 *Твой дашборд готов!*\n\n"
        "Нажми кнопку ниже — откроется прямо в Telegram ↓",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📊 Дашборд",
                web_app=WebAppInfo(url=_webapp_url(token, "dashboard")),
            )],
            [InlineKeyboardButton(
                text="🎯 Цели",
                web_app=WebAppInfo(url=_webapp_url(token, "goals")),
            ),
            InlineKeyboardButton(
                text="📋 История",
                web_app=WebAppInfo(url=_webapp_url(token, "history")),
            )],
            [InlineKeyboardButton(
                text="👤 Профиль",
                web_app=WebAppInfo(url=_webapp_url(token, "profile")),
            )],
        ]),
    )
