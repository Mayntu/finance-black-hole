from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

from core.config import settings


def skip_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard: includes Mini App shortcuts when HTTPS WEBAPP_URL is set."""
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="📊 Сегодня"), KeyboardButton(text="📈 Неделя")],
    ]
    base = (settings.WEBAPP_URL or settings.WEBHOOK_URL or "").rstrip("/")
    if base.startswith("https://"):
        rows.append([
            KeyboardButton(text="🌐 Дашборд", web_app=WebAppInfo(url=f"{base}/dashboard")),
            KeyboardButton(text="🌐 Цели", web_app=WebAppInfo(url=f"{base}/goals")),
        ])
    rows.extend([
        [KeyboardButton(text="🎯 Цели"), KeyboardButton(text="🎖 Миссии")],
        [KeyboardButton(text="🧠 Инсайт"), KeyboardButton(text="📂 Категории")],
        [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="💳 Бюджет")],
    ])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def mission_complete_keyboard(mission_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"mission_done:{mission_id}")]
        ]
    )


def categories_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")]
        for cat in categories
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
