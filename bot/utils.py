"""Shared utilities for bot handlers."""
import re

# ── Skip intent ────────────────────────────────────────────────────────────────

_SKIP_VARIANTS = {
    "пропустить", "пропускаю", "пропускай", "пропусти", "пропуск",
    "skip", "нет", "не надо", "не нужно", "не хочу", "не знаю",
    "стандарт", "стандартные", "дефолт", "default",
    "ок", "ok", "okay", "ладно", "хорошо", "потом", "позже",
    "pass", "no", "+", "-", ".",
}


def is_skip(text: str) -> bool:
    t = text.lower().strip().rstrip("!.,;:")
    return t in _SKIP_VARIANTS or len(t) <= 2


# ── Amount extraction ─────────────────────────────────────────────────────────

def extract_amount(text: str) -> float | None:
    """
    Parse amounts from natural language:
    450000 / 450к / 450 000 / 450тыс / 1.5млн / 1,5к
    """
    text = text.replace("\u00a0", " ").replace(",", ".")

    patterns = [
        (r"(\d[\d\s.]*)(?:млн|миллион)", 1_000_000),
        (r"(\d[\d\s.]*)(?:тыс|тысяч)", 1_000),
        (r"(\d[\d\s.]*)[кКkK]\b", 1_000),
        (r"(\d[\d\s.]+)", 1),
    ]
    for pattern, multiplier in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(" ", "")) * multiplier
                if val >= 1:
                    return val
            except ValueError:
                pass
    return None


def clean_title(text: str) -> str:
    """Remove amount substrings to get a readable goal title."""
    cleaned = re.sub(
        r"\d[\d\s.]*(?:млн|тыс|тысяч|[кКkK]\b)?", "", text, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip("., -")
    return cleaned if len(cleaned) > 2 else text.strip()


# ── Savings intent ─────────────────────────────────────────────────────────────

_SAVINGS_KEYWORDS = [
    "отложил", "отложила", "накопил", "накопила",
    "положил", "положила", "пополнил", "пополнила",
    "добавил", "добавила", "сохранил", "сохранила",
    "засобирал", "засобирала",
    "в копилку", "в заначку", "в копилке",
    "к цели", "на цель", "на накопления", "на счёт",
    "отложить", "накопить", "сберечь",
]


def is_savings_intent(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _SAVINGS_KEYWORDS)
