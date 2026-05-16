from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from models.achievement import Achievement
from models.user import User

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

LEVELS = [
    {"level": 1, "name": "Импульсивный", "xp_required": 0,    "emoji": "🌊"},
    {"level": 2, "name": "Наблюдатель",  "xp_required": 100,  "emoji": "👁️"},
    {"level": 3, "name": "Осознанный",   "xp_required": 300,  "emoji": "🧭"},
    {"level": 4, "name": "Стратег",      "xp_required": 700,  "emoji": "⚡"},
    {"level": 5, "name": "Инвестор",     "xp_required": 1500, "emoji": "🏔️"},
]

XP_REWARDS = {
    "expense_logged":   10,
    "conscious_expense": 5,
    "daily_streak":     15,
    "mission_easy":     70,
    "mission_medium":   130,
    "mission_hard":     190,
    "goal_reached":     500,
    "no_regret_day":    50,
}

ACHIEVEMENTS: dict[str, dict] = {
    "first_entry":     {"emoji": "🌱", "title": "Первый шаг",          "desc": "Внёс первую трату"},
    "streak_3":        {"emoji": "🔥", "title": "3 дня подряд",        "desc": "3 дня без пропуска"},
    "streak_7":        {"emoji": "💫", "title": "Неделя осознанности", "desc": "7 дней подряд"},
    "streak_30":       {"emoji": "💎", "title": "Месяц контроля",      "desc": "30 дней подряд"},
    "first_mission":   {"emoji": "⚡", "title": "Первая миссия",       "desc": "Закрыл первую миссию"},
    "goal_reached":    {"emoji": "🏆", "title": "Цель достигнута",     "desc": "Достиг финансовой цели"},
    "no_regret_day":   {"emoji": "✨", "title": "День без сожалений",  "desc": "Все траты осознанные за день"},
    "personality_up":  {"emoji": "🧬", "title": "Эволюция",           "desc": "Финансовая личность выросла"},
    "insight_received":{"emoji": "🧠", "title": "Момент истины",      "desc": "Получил первый инсайт"},
}


def get_level_info(xp: int) -> dict:
    """Return current level info and progress to next level."""
    current = LEVELS[0]
    for lvl in LEVELS:
        if xp >= lvl["xp_required"]:
            current = lvl
        else:
            break

    current_idx = current["level"] - 1
    next_lvl = LEVELS[current_idx + 1] if current_idx + 1 < len(LEVELS) else None

    if next_lvl:
        xp_in_level = xp - current["xp_required"]
        xp_needed = next_lvl["xp_required"] - current["xp_required"]
        progress_pct = min(100, int(xp_in_level / xp_needed * 100))
    else:
        xp_in_level = xp - current["xp_required"]
        xp_needed = 0
        progress_pct = 100

    return {
        "level": current["level"],
        "name": current["name"],
        "emoji": current["emoji"],
        "next_level": next_lvl,
        "xp_in_level": xp_in_level,
        "xp_needed": xp_needed,
        "progress_pct": progress_pct,
    }


def format_progress_bar(pct: int, length: int = 10) -> str:
    filled = int(length * pct / 100)
    return "█" * filled + "░" * (length - filled)


class GamificationService:
    async def add_xp(self, user: User, xp: int, session: AsyncSession) -> tuple[int, bool]:
        """Add XP, return (new_xp, leveled_up)."""
        old_level = user.level
        user.xp += xp

        info = get_level_info(user.xp)
        new_level = info["level"]
        leveled_up = new_level > old_level
        if leveled_up:
            user.level = new_level
            logger.info("level_up", user_id=user.id, new_level=new_level)

        session.add(user)
        return user.xp, leveled_up

    async def update_streak(self, user: User, session: AsyncSession) -> tuple[int, bool]:
        """Update daily streak. Returns (streak_days, streak_broken)."""
        today = date.today()
        streak_broken = False

        if user.last_active_date is None:
            user.streak_days = 1
        elif user.last_active_date == today:
            # Already logged today — streak bonus for daily_streak given on first log only
            pass
        elif user.last_active_date == today.replace(day=today.day - 1) or (
            today.toordinal() - user.last_active_date.toordinal() == 1
        ):
            user.streak_days += 1
        else:
            streak_broken = user.streak_days > 0
            user.streak_days = 1

        user.last_active_date = today
        session.add(user)
        return user.streak_days, streak_broken

    async def check_and_grant_achievements(
        self, user: User, session: AsyncSession, expense_count: int
    ) -> list[str]:
        """Check conditions and grant new achievements. Returns list of new achievement codes."""
        existing_codes = {a.code for a in user.achievements}
        new_codes: list[str] = []

        async def grant(code: str) -> None:
            if code not in existing_codes:
                ach = Achievement(user_id=user.id, code=code)
                session.add(ach)
                new_codes.append(code)
                existing_codes.add(code)

        if expense_count == 1:
            await grant("first_entry")

        if user.streak_days >= 3:
            await grant("streak_3")
        if user.streak_days >= 7:
            await grant("streak_7")
        if user.streak_days >= 30:
            await grant("streak_30")

        return new_codes

    def format_achievement_notification(self, codes: list[str]) -> str:
        parts = []
        for code in codes:
            info = ACHIEVEMENTS.get(code)
            if info:
                parts.append(f"{info['emoji']} **{info['title']}** — {info['desc']}")
        return "\n".join(parts)

    def format_expense_card(
        self,
        amount: float,
        currency: str,
        label: str,
        category: str,
        is_conscious: bool | None,
        xp_gained: int,
        streak_days: int,
        user_xp: int,
        active_goal_title: str | None = None,
        active_goal_target: float | None = None,
    ) -> str:
        consciousness_line = ""
        if is_conscious is True:
            consciousness_line = "💭 Осознанная трата"
        elif is_conscious is False:
            consciousness_line = "💭 Импульсивная трата"

        goal_line = ""
        if active_goal_title and active_goal_target and active_goal_target > 0:
            pct = round((amount / active_goal_target) * 100, 1)
            goal_line = f"🎯 = {pct}% от цели «{active_goal_title}»"

        lvl_info = get_level_info(user_xp)
        bar = format_progress_bar(lvl_info["progress_pct"])

        lines = [
            "✅ Записал!\n",
            f"💸 {amount:,.0f} {currency} — {label}",
            f"📂 {category}",
        ]
        if consciousness_line:
            lines.append(consciousness_line)
        if goal_line:
            lines.append(goal_line)
        lines.append("")
        lines.append(f"⭐ +{xp_gained} XP  •  🔥 Стрик: {streak_days} {'день' if streak_days == 1 else 'дня' if 2 <= streak_days <= 4 else 'дней'}")
        lines.append(f"{bar}  Уровень {lvl_info['level']}: {lvl_info['name']}")

        return "\n".join(lines)


gamification_service = GamificationService()
