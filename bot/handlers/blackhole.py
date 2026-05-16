from datetime import date, datetime

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.keyboards import categories_keyboard
from bot.states import ClarifyExpenseState
from bot.utils import extract_amount, is_savings_intent
from models.achievement import Achievement
from models.expense import Expense
from models.goal import Goal
from models.mission import Mission
from models.user import User
from services.ai_service import ai_service
from services.analytics import analytics_service
from services.expense_service import expense_service
from services.gamification import ACHIEVEMENTS, XP_REWARDS, format_progress_bar, get_level_info, gamification_service

logger = structlog.get_logger(__name__)
router = Router()

DEFAULT_CATEGORIES = ["Еда", "Транспорт", "Развлечения", "Здоровье", "Одежда", "Коммунальные", "Подписки", "Прочее"]
_MENU_TEXTS = {
    "📊 Сегодня", "📈 Неделя", "🎯 Цели", "👤 Профиль", "🎖 Миссии",
    "🌐 Дашборд", "🌐 Цели", "🧠 Инсайт", "📂 Категории", "💳 Бюджет", "❓ Помощь",
}


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.achievements))
    )
    return result.scalar_one_or_none()


async def _get_active_goal(session: AsyncSession, user_id: int) -> Goal | None:
    result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user_id, Goal.is_active == True, Goal.is_completed == False)
        .order_by(Goal.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


# ── Inline keyboard for expense card ──────────────────────────────────────────

def _expense_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{expense_id}"),
        InlineKeyboardButton(text="🗑 Удалить",  callback_data=f"del:{expense_id}"),
    ]])


# ── Savings handler ────────────────────────────────────────────────────────────

async def _handle_savings(message: Message, session: AsyncSession, user: User, amount: float) -> None:
    goal, just_completed = await expense_service.add_savings_to_goal(session, user.id, amount)

    if not goal:
        await message.answer(
            "💡 Нет активной цели для накоплений.\n"
            "Создай цель командой /goal — и каждое отложение будет идти к ней."
        )
        return

    pct = min(100, int(goal.current_amount / goal.target_amount * 100)) if goal.target_amount > 0 else 0
    bar = format_progress_bar(pct)
    remaining = max(0, goal.target_amount - goal.current_amount)
    xp_gained = XP_REWARDS["expense_logged"]
    _, leveled_up = await gamification_service.add_xp(user, xp_gained, session)

    await message.answer(
        f"💰 *Сохранено!*\n\n"
        f"➕ {amount:,.0f} ₸ → *{goal.title}*\n\n"
        f"{bar}  {pct}%\n"
        f"{goal.current_amount:,.0f} / {goal.target_amount:,.0f} ₸\n"
        f"До цели: {remaining:,.0f} ₸\n\n"
        f"⭐ +{xp_gained} XP",
        parse_mode="Markdown",
    )

    if just_completed:
        extra_xp = XP_REWARDS["goal_reached"]
        await gamification_service.add_xp(user, extra_xp, session)
        existing = {a.code for a in user.achievements}
        if "goal_reached" not in existing:
            session.add(Achievement(user_id=user.id, code="goal_reached"))
        info = ACHIEVEMENTS["goal_reached"]
        await message.answer(
            f"🏆 *ЦЕЛЬ ДОСТИГНУТА!*\n\n"
            f"🎯 *{goal.title}* — {goal.target_amount:,.0f} ₸\n\n"
            f"⭐ +{extra_xp} XP  •  {info['emoji']} {info['title']}",
            parse_mode="Markdown",
        )

    if leveled_up:
        info = get_level_info(user.xp)
        await message.answer(
            f"🎉 *Уровень повышен!* Теперь ты {info['emoji']} *{info['name']}* — уровень {info['level']}",
            parse_mode="Markdown",
        )


# ── /save command ──────────────────────────────────────────────────────────────

@router.message(Command("save"))
async def cmd_save(message: Message, session: AsyncSession) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Привет! Напиши /start чтобы начать 👋")
        return
    args = message.text.split(maxsplit=1)
    amount = extract_amount(args[1]) if len(args) > 1 else None
    if not amount:
        await message.answer("Напиши сумму: _/save 5000_ или _/save 5к_", parse_mode="Markdown")
        return
    await _handle_savings(message, session, user, amount)


# ── AI tip helper ──────────────────────────────────────────────────────────────

async def _send_ai_tip(
    message: Message,
    session: AsyncSession,
    user: User,
    label: str,
    amount: float,
    category: str,
) -> None:
    """Send a personalized AI tip based on recent spending."""
    try:
        recent_result = await session.execute(
            select(Expense)
            .where(Expense.user_id == user.id)
            .order_by(Expense.created_at.desc())
            .limit(10)
        )
        recent = recent_result.scalars().all()
        summary_lines = []
        for e in recent[:10]:
            summary_lines.append(f"- {e.label or e.category}: {e.amount:,.0f} ₸")
        summary = "\n".join(summary_lines) if summary_lines else "нет данных"

        tip_data = await ai_service.generate_spending_tip(label, amount, category, summary)
        if not tip_data or not tip_data.get("tip"):
            return

        tip_type = tip_data.get("type", "suggestion")
        emoji = {"praise": "✨", "warning": "⚠️", "suggestion": "💡"}.get(tip_type, "💡")
        await message.answer(f"{emoji} _{tip_data['tip']}_", parse_mode="Markdown")
    except Exception as e:
        logger.warning("ai_tip_failed", error=str(e))


# ── Auto first-mission helper ───────────────────────────────────────────────────

async def _maybe_generate_first_mission(
    message: Message,
    session: AsyncSession,
    user: User,
) -> None:
    """Generate a first mission if user has 3+ expenses and no active mission."""
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)

    # Check active missions
    existing = await session.execute(
        select(Mission).where(
            Mission.user_id == user.id,
            Mission.is_completed == False,
            Mission.expires_at > now,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        return

    # Check total expense count
    count_result = await session.execute(
        select(func.count(Expense.id)).where(Expense.user_id == user.id)
    )
    total = count_result.scalar() or 0
    # Only trigger at exactly 3, 10, 20... to avoid generating every time
    if total not in {3, 10, 20, 40, 75}:
        return

    try:
        # Build weakness from categories
        cat_result = await session.execute(
            select(Expense.category, func.sum(Expense.amount).label("total"))
            .where(Expense.user_id == user.id)
            .group_by(Expense.category)
        )
        cat_totals = {r.category: r.total for r in cat_result.all()}
        weakness = max(cat_totals, key=cat_totals.get) if cat_totals else "Прочее"

        goal_result = await session.execute(
            select(Goal).where(Goal.user_id == user.id, Goal.is_active == True, Goal.is_completed == False).limit(1)
        )
        goal = goal_result.scalar_one_or_none()

        mission_data = await ai_service.generate_mission(
            goal=goal.title if goal else "нет цели",
            level=user.level,
            weakness=weakness,
        )
        mission = Mission(
            user_id=user.id,
            title=mission_data.title,
            description=mission_data.description,
            success_criteria=mission_data.success_criteria,
            why_this_mission=mission_data.why_this_mission,
            reward_xp=mission_data.reward_xp,
            difficulty=mission_data.difficulty,
            expires_at=now + timedelta(days=7),
        )
        session.add(mission)
        await session.flush()

        diff_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(mission_data.difficulty, "⚡")
        await message.answer(
            f"🎖 *Новая персональная миссия!*\n\n"
            f"*{mission_data.title}* {diff_emoji}\n"
            f"{mission_data.description}\n\n"
            f"✅ _{mission_data.success_criteria}_\n"
            f"💡 {mission_data.why_this_mission}\n\n"
            f"Награда: ⭐ {mission_data.reward_xp} XP • 7 дней",
            parse_mode="Markdown",
        )
        logger.info("auto_mission_generated", user_id=user.id, count=total)
    except Exception as e:
        logger.warning("auto_mission_failed", error=str(e))


# ── Core: save expense + respond ───────────────────────────────────────────────

async def _save_and_respond(
    message: Message,
    session: AsyncSession,
    user: User,
    raw_text: str,
    parsed,
) -> None:
    """Save expense to DB, award XP, send card with edit/delete buttons."""
    active_goal = await _get_active_goal(session, user.id)

    if parsed.amount is None:
        await message.answer(
            f"🤔 Понял: *{parsed.label}* — но не нашёл сумму.\n\n"
            "Напиши сколько заплатил (только число, например *1500*):",
            parse_mode="Markdown",
        )
        return

    if parsed.is_conscious is None and parsed.amount is not None:
        try:
            parsed.is_conscious = await ai_service.judge_expense_consciousness(
                label=parsed.label,
                amount=float(parsed.amount),
                category=parsed.category,
                extra_context=None,
                thresholds=user.conscious_thresholds or None,
            )
        except Exception:
            pass

    expense = await expense_service.create_expense(
        session=session,
        user=user,
        raw_input=raw_text,
        amount=parsed.amount,
        category=parsed.category,
        label=parsed.label,
        ai_confidence=parsed.confidence,
        is_conscious=parsed.is_conscious,
    )

    # XP
    xp_gained = XP_REWARDS["expense_logged"]
    if parsed.is_conscious is True:
        xp_gained += XP_REWARDS["conscious_expense"]

    is_first_today = user.last_active_date != date.today()
    streak_days, streak_broken = await gamification_service.update_streak(user, session)
    if is_first_today:
        xp_gained += XP_REWARDS["daily_streak"]

    _, leveled_up = await gamification_service.add_xp(user, xp_gained, session)
    count = await expense_service.get_expense_count(session, user.id)
    new_achievements = await gamification_service.check_and_grant_achievements(user, session, count)

    card = gamification_service.format_expense_card(
        amount=parsed.amount,
        currency=parsed.currency,
        label=parsed.label,
        category=parsed.category,
        is_conscious=parsed.is_conscious,
        xp_gained=xp_gained,
        streak_days=streak_days,
        user_xp=user.xp,
        active_goal_title=active_goal.title if active_goal else None,
        active_goal_target=active_goal.target_amount if active_goal else None,
    )

    await message.answer(card, parse_mode="Markdown", reply_markup=_expense_keyboard(expense.id))

    if leveled_up:
        info = get_level_info(user.xp)
        await message.answer(
            f"🎉 *Уровень повышен!*\n\nТеперь ты {info['emoji']} *{info['name']}* — уровень {info['level']}",
            parse_mode="Markdown",
        )
    if new_achievements:
        notif = gamification_service.format_achievement_notification(new_achievements)
        await message.answer(f"🏅 *Новое достижение!*\n\n{notif}", parse_mode="Markdown")
    if streak_broken:
        await message.answer("😬 Стрик прервался — пропустил день. Начинаем заново! 💪")

    logger.info("expense_logged", user_id=user.id, amount=parsed.amount, xp=xp_gained)

    if analytics_service.should_update_personality(user, count):
        await _update_personality(message, session, user)

    # ── AI tip every ~5th expense ──────────────────────────────────────────────
    if count % 5 == 0:
        await _send_ai_tip(message, session, user, parsed.label, parsed.amount, parsed.category)

    # ── Auto-generate first mission when 3+ expenses and none active ──────────
    if count >= 3:
        await _maybe_generate_first_mission(message, session, user)


# ── Core: parse text → expense ─────────────────────────────────────────────────

async def _process_expense_text(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    text: str,
) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Привет! Напиши /start чтобы начать 👋")
        return

    if is_savings_intent(text):
        amount = extract_amount(text)
        if amount:
            await _handle_savings(message, session, user, amount)
            return

    try:
        parsed = await ai_service.parse_expense(
            text,
            user.custom_categories or [],
            thresholds=user.conscious_thresholds or None,
        )
    except Exception:
        await message.answer("😔 AI недоступен, попробуй через минуту.")
        return

    if not parsed.understood:
        await message.answer(
            "🤔 Не понял что имеешь в виду.\n\n"
            "Пиши как другу: *кофе 300*, *такси до офиса 1200*, *продукты 3500*",
            parse_mode="Markdown",
        )
        return

    # ── FIX: amount is None — ask user ────────────────────────────────────────
    if parsed.amount is None:
        await state.update_data(
            pending_raw=text,
            pending_label=parsed.label,
            pending_category=parsed.category,
            pending_is_conscious=parsed.is_conscious,
            pending_confidence=parsed.confidence,
        )
        await state.set_state(ClarifyExpenseState.waiting_amount)
        await message.answer(
            f"🤔 Понял: *{parsed.label}* ({parsed.category})\n\n"
            "💬 Сколько заплатил? Напиши сумму:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if parsed.confidence < 0.75:
        question = parsed.clarification_needed or "Уточни — что именно и на сколько?"
        await state.update_data(pending_raw=text)
        await message.answer(f"🤔 {question}", reply_markup=ReplyKeyboardRemove())
        await state.set_state(ClarifyExpenseState.waiting_clarification)
        return

    await _save_and_respond(message, session, user, text, parsed)


# ── FSM: waiting for amount ────────────────────────────────────────────────────

@router.message(ClarifyExpenseState.waiting_amount)
async def handle_amount_clarification(message: Message, state: FSMContext, session: AsyncSession) -> None:
    amount = extract_amount(message.text)
    if not amount:
        await message.answer("Введи число: например *1500* или *1.5к*", parse_mode="Markdown")
        return

    data = await state.get_data()
    await state.clear()

    user = await _get_user(session, message.from_user.id)
    if not user:
        return

    class _FakeParsed:
        understood = True
        is_conscious = None
        clarification_needed = None

    parsed = _FakeParsed()
    parsed.amount = float(amount)
    parsed.currency = "KZT"
    parsed.category = data.get("pending_category", "Прочее")
    parsed.label = data.get("pending_label", message.text)
    parsed.confidence = data.get("pending_confidence", 0.9)
    parsed.is_conscious = data.get("pending_is_conscious")

    await _save_and_respond(message, session, user, data.get("pending_raw", message.text), parsed)


# ── FSM: waiting for clarification ────────────────────────────────────────────

@router.message(ClarifyExpenseState.waiting_clarification)
async def handle_clarification(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    original = data.get("pending_raw", "")
    combined = f"{original}. Уточнение: {message.text}"
    await state.clear()

    user = await _get_user(session, message.from_user.id)
    if not user:
        return

    try:
        parsed = await ai_service.parse_expense(
            combined,
            user.custom_categories or [],
            thresholds=user.conscious_thresholds or None,
        )
    except Exception:
        await message.answer("😔 AI недоступен, попробуй через минуту.")
        return

    if not parsed.understood or parsed.amount is None:
        cats = user.custom_categories or DEFAULT_CATEGORIES
        await message.answer("Всё равно не понял 😔 Выбери категорию вручную:", reply_markup=categories_keyboard(cats[:8]))
        return

    await _save_and_respond(message, session, user, combined, parsed)


# ── FSM: waiting for edit text ─────────────────────────────────────────────────

@router.message(ClarifyExpenseState.waiting_edit_text)
async def handle_edit_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    expense_id = data.get("edit_expense_id")
    await state.clear()

    if not expense_id:
        await message.answer("Что-то пошло не так, попробуй снова.")
        return

    result = await session.execute(select(Expense).where(Expense.id == expense_id))
    expense = result.scalar_one_or_none()
    if not expense:
        await message.answer("Трата не найдена.")
        return

    user = await _get_user(session, message.from_user.id)
    if not user or expense.user_id != user.id:
        return

    try:
        parsed = await ai_service.parse_expense(
            message.text,
            user.custom_categories or [],
            thresholds=user.conscious_thresholds or None,
        )
    except Exception:
        await message.answer("😔 AI недоступен.")
        return

    if not parsed.understood or parsed.amount is None:
        await message.answer("Не понял. Напиши как обычную трату: *кофе 350*", parse_mode="Markdown")
        return

    expense.raw_input = message.text
    expense.amount = parsed.amount
    expense.category = parsed.category
    expense.label = parsed.label
    expense.is_conscious = parsed.is_conscious
    expense.ai_confidence = parsed.confidence
    session.add(expense)

    await message.answer(
        f"✅ *Трата обновлена*\n\n"
        f"💸 {parsed.amount:,.0f} ₸ — {parsed.label}\n"
        f"📂 {parsed.category}",
        parse_mode="Markdown",
        reply_markup=_expense_keyboard(expense.id),
    )


# ── Callbacks: edit / delete ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("del:"))
async def cb_delete_expense(call: CallbackQuery, session: AsyncSession) -> None:
    expense_id = int(call.data.split(":")[1])
    result = await session.execute(select(Expense).where(Expense.id == expense_id))
    expense = result.scalar_one_or_none()

    if not expense:
        await call.answer("Трата не найдена.", show_alert=True)
        return

    user = await _get_user(session, call.from_user.id)
    if not user or expense.user_id != user.id:
        await call.answer("Нет доступа.", show_alert=True)
        return

    label = expense.label
    amount = expense.amount
    await session.delete(expense)

    await call.message.edit_text(
        f"🗑 *Трата удалена*\n\n~~{label} — {amount:,.0f} ₸~~",
        parse_mode="Markdown",
    )
    await call.answer("Удалено ✓")


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit_expense(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    expense_id = int(call.data.split(":")[1])
    result = await session.execute(select(Expense).where(Expense.id == expense_id))
    expense = result.scalar_one_or_none()

    if not expense:
        await call.answer("Трата не найдена.", show_alert=True)
        return

    user = await _get_user(session, call.from_user.id)
    if not user or expense.user_id != user.id:
        await call.answer("Нет доступа.", show_alert=True)
        return

    await state.update_data(edit_expense_id=expense_id)
    await state.set_state(ClarifyExpenseState.waiting_edit_text)

    await call.message.answer(
        f"✏️ *Редактируем трату:*\n"
        f"_{expense.label} — {expense.amount:,.0f} ₸_\n\n"
        "Напиши новое описание (как обычную трату):",
        parse_mode="Markdown",
    )
    await call.answer()


# ── Voice messages ─────────────────────────────────────────────────────────────

@router.message(F.voice | F.audio)
async def handle_voice(message: Message, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    file_obj = message.voice or message.audio
    thinking = await message.answer("🎙 Слушаю...")

    try:
        file = await bot.get_file(file_obj.file_id)
        audio_bytes = await bot.download_file(file.file_path)
        audio_data = audio_bytes.read()
    except Exception:
        await thinking.edit_text("😔 Не удалось скачать аудио.")
        return

    try:
        text = await ai_service.transcribe_voice(audio_data, "voice.ogg")
    except Exception:
        await thinking.edit_text("😔 Ошибка транскрипции. Попробуй ещё раз.")
        return

    if not text:
        await thinking.edit_text("🤔 Ничего не расслышал. Говори чётче.")
        return

    await thinking.edit_text(f"🎙 *Распознал:* _{text}_\n\nОбрабатываю...", parse_mode="Markdown")
    await _process_expense_text(message, session, state, text)


# ── Photo / receipt ────────────────────────────────────────────────────────────

@router.message(F.photo)
async def handle_photo(message: Message, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся /start")
        return

    thinking = await message.answer("🧾 Читаю чек...")

    # Download the largest photo
    photo = message.photo[-1]
    try:
        file = await bot.get_file(photo.file_id)
        img_bytes_io = await bot.download_file(file.file_path)
        img_bytes = img_bytes_io.read()
    except Exception:
        await thinking.edit_text("😔 Не удалось скачать фото.")
        return

    try:
        receipt = await ai_service.parse_receipt_image(img_bytes)
    except Exception:
        await thinking.edit_text("😔 Ошибка при анализе чека. Попробуй ещё раз.")
        return

    if not receipt.get("understood"):
        await thinking.edit_text(
            "🤔 Не похоже на чек или ценник.\n\n"
            "Сфотографируй чек из магазина или просто напиши что потратил."
        )
        return

    total = receipt.get("total")
    items = receipt.get("items", [])
    store = receipt.get("store_name", "")
    currency = receipt.get("currency", "KZT")

    # Build preview
    store_line = f"🏪 *{store}*\n" if store else ""
    if items:
        items_text = "\n".join(f"  • {i['label']} — {i['amount']:,.0f} ₸" for i in items[:5])
        if len(items) > 5:
            items_text += f"\n  _...и ещё {len(items)-5} позиций_"
    else:
        items_text = ""

    if total:
        # Store receipt data in FSM — callback_data must be ≤64 bytes
        category = items[0]["category"] if items else "Прочее"
        label = store or (items[0]["label"] if items else "Чек")
        await state.update_data(
            receipt_amount=float(total),
            receipt_category=category,
            receipt_label=label[:40],
            receipt_currency=currency,
            receipt_store=(store[:120] if store else None),
        )

        preview = (
            f"🧾 *Чек распознан*\n\n"
            f"{store_line}"
            f"{items_text}\n\n"
            f"💸 *Итого: {total:,.0f} {currency}*\n\n"
            "Записать эту трату?"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Записать", callback_data="receipt_ok"),
            InlineKeyboardButton(text="❌ Отмена",   callback_data="receipt_cancel"),
        ]])
        await thinking.edit_text(preview, parse_mode="Markdown", reply_markup=keyboard)
    else:
        # No total — use first item or ask user
        if items:
            item = items[0]
            text = f"{item['label']} {int(item['amount'])}"
            await thinking.edit_text(f"🧾 Записываю: _{text}_", parse_mode="Markdown")
            await _process_expense_text(message, session, state, text)
        else:
            await thinking.edit_text("Не удалось определить сумму. Напиши трату вручную.")


@router.callback_query(F.data == "receipt_ok")
async def cb_receipt_ok(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    amount = data.get("receipt_amount")
    category = data.get("receipt_category", "Прочее")
    label = data.get("receipt_label", "Чек")
    currency = data.get("receipt_currency", "KZT")
    store_hint = data.get("receipt_store")

    if not amount:
        await call.answer("Данные чека не найдены, попробуй ещё раз.", show_alert=True)
        return

    user = await _get_user(session, call.from_user.id)
    if not user:
        await call.answer("Пользователь не найден.", show_alert=True)
        return

    await state.update_data(
        receipt_amount=None,
        receipt_category=None,
        receipt_label=None,
        receipt_currency=None,
        receipt_store=None,
    )
    class _FakeParsed:
        understood = True
        confidence = 0.95
        clarification_needed = None

    parsed = _FakeParsed()
    parsed.amount = float(amount)
    parsed.currency = currency
    parsed.category = category
    parsed.label = label
    try:
        parsed.is_conscious = await ai_service.judge_expense_consciousness(
            label=label,
            amount=float(amount),
            category=category,
            extra_context=store_hint,
            thresholds=user.conscious_thresholds or None,
        )
    except Exception:
        parsed.is_conscious = None

    await call.message.edit_reply_markup(reply_markup=None)
    await _save_and_respond(call.message, session, user, label, parsed)
    await call.answer()


@router.callback_query(F.data == "receipt_cancel")
async def cb_receipt_cancel(call: CallbackQuery) -> None:
    await call.message.edit_text("Отменено.")
    await call.answer()


# ── Personality update ─────────────────────────────────────────────────────────

async def _update_personality(message: Message, session: AsyncSession, user: User) -> None:
    try:
        context = await analytics_service.build_personality_context(session, user.id)
        personality = await ai_service.evaluate_financial_personality(context)

        old = user.financial_personality
        user.financial_personality = personality.archetype
        from datetime import datetime, timezone
        user.last_personality_update = datetime.now(timezone.utc)
        user.personality_data = {
            "emoji": personality.emoji,
            "description": personality.description,
            "dominant_pattern": personality.dominant_pattern,
            "growth_hint": personality.growth_hint,
        }
        session.add(user)

        if old and old != personality.archetype:
            existing = {a.code for a in user.achievements}
            if "personality_up" not in existing:
                session.add(Achievement(user_id=user.id, code="personality_up"))
            info = ACHIEVEMENTS["personality_up"]
            await message.answer(
                f"🧬 *Финансовая личность изменилась!*\n\n"
                f"{personality.emoji} *{old}* → *{personality.archetype}*\n\n"
                f"{personality.description}\n\n"
                f"{info['emoji']} Ачивка: _{info['title']}_",
                parse_mode="Markdown",
            )
        elif old is None:
            await message.answer(
                f"💡 *Твоя финансовая личность определена!*\n\n"
                f"{personality.emoji} *{personality.archetype}*\n\n"
                f"{personality.description}\n\n"
                f"_Доминирующий паттерн: {personality.dominant_pattern}_",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.warning("personality_update_failed", user_id=user.id, error=str(e))


# ── Main text handler (catches everything not matched above) ───────────────────

@router.message(F.text, ~F.text.startswith("/"), ~F.text.in_(_MENU_TEXTS))
async def blackhole_handler(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _process_expense_text(message, session, state, message.text)
