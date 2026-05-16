from datetime import datetime, timezone

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.keyboards import mission_complete_keyboard
from models.achievement import Achievement
from models.mission import Mission
from models.user import User
from services.gamification import ACHIEVEMENTS, gamification_service, get_level_info

logger = structlog.get_logger(__name__)
router = Router()

_DIFFICULTY_EMOJI = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
_DIFFICULTY_LABEL = {"easy": "Лёгкая", "medium": "Средняя", "hard": "Сложная"}


def _format_mission_card(mission: Mission) -> str:
    diff_emoji = _DIFFICULTY_EMOJI.get(mission.difficulty, "⚪")
    diff_label = _DIFFICULTY_LABEL.get(mission.difficulty, "")
    days_left = max(0, (mission.expires_at - datetime.now(timezone.utc)).days)
    return (
        f"{diff_emoji} *{mission.title}*  _{diff_label}_\n\n"
        f"{mission.description}\n\n"
        f"✅ *Условие:* {mission.success_criteria}\n"
        f"💡 _{mission.why_this_mission}_\n\n"
        f"⭐ {mission.reward_xp} XP  •  ⏳ {days_left} {'день' if days_left == 1 else 'дня' if 2 <= days_left <= 4 else 'дней'}"
    )


@router.message(Command("missions"))
@router.message(F.text == "🎖 Миссии")
async def cmd_missions(message: Message, session: AsyncSession) -> None:
    result = await session.execute(
        select(User).where(User.telegram_id == message.from_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Mission).where(
            Mission.user_id == user.id,
            Mission.is_completed == False,
            Mission.expires_at > now,
        ).order_by(Mission.created_at.desc())
    )
    missions = result.scalars().all()

    if not missions:
        await message.answer(
            "🎖 *Миссий пока нет*\n\n"
            "Каждое воскресенье AI анализирует твои траты и генерирует персональные задания.\n\n"
            "Трекай траты регулярно — и через неделю получишь первую миссию!\n\n"
            "_Записывай траты каждый день 💪_",
            parse_mode="Markdown",
        )
        return

    await message.answer(
        f"🎖 *Активные миссии — {len(missions)}:*",
        parse_mode="Markdown",
    )
    for mission in missions:
        await message.answer(
            _format_mission_card(mission),
            parse_mode="Markdown",
            reply_markup=mission_complete_keyboard(mission.id),
        )


@router.callback_query(F.data.startswith("mission_done:"))
async def mission_done_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    mission_id = int(callback.data.split(":")[1])

    result = await session.execute(
        select(Mission).where(Mission.id == mission_id)
    )
    mission = result.scalar_one_or_none()

    if not mission:
        await callback.answer("Миссия не найдена")
        return

    result = await session.execute(
        select(User)
        .where(User.telegram_id == callback.from_user.id)
        .options(selectinload(User.achievements))
    )
    user = result.scalar_one_or_none()

    if not user or mission.user_id != user.id:
        await callback.answer("Это не твоя миссия 🤨")
        return

    if mission.is_completed:
        await callback.answer("Уже выполнено ✅")
        return

    if datetime.now(timezone.utc) > mission.expires_at:
        await callback.answer("Время миссии истекло ⏰")
        return

    # Mark complete
    mission.is_completed = True
    session.add(mission)

    # Award XP
    _, leveled_up = await gamification_service.add_xp(user, mission.reward_xp, session)

    # Check first_mission achievement
    existing_codes = {a.code for a in user.achievements}
    new_achievement = None
    if "first_mission" not in existing_codes:
        session.add(Achievement(user_id=user.id, code="first_mission"))
        new_achievement = "first_mission"

    diff_emoji = _DIFFICULTY_EMOJI.get(mission.difficulty, "⚪")

    try:
        await callback.message.edit_text(
            f"✅ *Миссия выполнена!*\n\n"
            f"{diff_emoji} {mission.title}\n\n"
            f"⭐ +{mission.reward_xp} XP получено!",
            parse_mode="Markdown",
        )
    except Exception:
        pass  # Message might be too old to edit

    if leveled_up:
        info = get_level_info(user.xp)
        await callback.message.answer(
            f"🎉 *Уровень повышен!*\n\n"
            f"Теперь ты {info['emoji']} *{info['name']}* — уровень {info['level']}",
            parse_mode="Markdown",
        )

    if new_achievement:
        info = ACHIEVEMENTS[new_achievement]
        await callback.message.answer(
            f"🏅 *{info['emoji']} {info['title']}* — {info['desc']}",
            parse_mode="Markdown",
        )

    await callback.answer("🎉 Выполнено! Отличная работа!")
    logger.info("mission_completed", user_id=user.id, mission_id=mission_id, xp=mission.reward_xp)
