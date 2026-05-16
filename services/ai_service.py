import hashlib
import json
from dataclasses import dataclass
from typing import Any

import structlog
from openai import AsyncOpenAI

from core.config import settings
from core.redis import get_redis

logger = structlog.get_logger(__name__)

CACHE_TTL = 3600  # 1 hour

# Default per-category thresholds (₸).
# IMPORTANT: these apply ONLY to single discretionary items (one coffee, one
# snack, one meal, one clothing piece). They do NOT apply to bulk/regular
# shopping (weekly groceries, household restocking, multi-item baskets).
# "Прочее" and custom categories are intentionally absent — judged by semantics.
DEFAULT_CONSCIOUS_THRESHOLDS: dict[str, int] = {
    "Еда":          2_000,   # single café/restaurant item or takeaway
    "Транспорт":    5_000,   # single ride; monthly pass is always conscious
    "Развлечения":  5_000,   # one event/game/experience
    "Здоровье":    15_000,   # single non-prescription purchase
    "Одежда":      15_000,   # one clothing/accessory item
    "Коммунальные": 30_000,  # utility bill (usually always conscious)
    "Подписки":     3_000,   # single subscription
}


# Per-category scenario guidance injected into AI prompts.
_CATEGORY_SCENARIOS = """
Сценарные правила по категориям (важнее простого числового порога):

Еда:
  ОСОЗНАННАЯ — продукты домой (супермаркет, рынок), закупка на неделю, готовка дома, даже на большую сумму.
  ИМПУЛЬСИВНАЯ — кофе, снек, один напиток, фастфуд, одна позиция в кафе/ресторане по завышенной цене.
  Ключевой сигнал: «продукты», «закупился», «магазин» = осознанно; «кофе», «перекус», «кафе», «ресторан» одним словом = проверь порог.

Транспорт:
  ОСОЗНАННАЯ — проездной, регулярный маршрут (дом-работа), плановая поездка, бензин полный бак.
  ИМПУЛЬСИВНАЯ — случайное такси без необходимости, один дорогой трансфер ради удобства.

Развлечения:
  ОСОЗНАННАЯ — запланированное мероприятие, билеты куплены заранее, хобби с регулярной практикой.
  ИМПУЛЬСИВНАЯ — спонтанный поход, случайная покупка, «а вдруг понравится».

Одежда:
  ОСОЗНАННАЯ — плановая замена изношенной вещи, покупка под конкретную потребность.
  ИМПУЛЬСИВНАЯ — спонтанная покупка по акции, «просто понравилось», модный аксессуар.

Здоровье:
  ОСОЗНАННАЯ — назначенные лекарства, плановый врач, регулярные процедуры.
  ИМПУЛЬСИВНАЯ — дорогой БАД без назначения, косметическая процедура по настроению.

Подписки:
  ОСОЗНАННАЯ — используемый сервис, регулярный платёж.
  ИМПУЛЬСИВНАЯ — новая подписка «попробовать», давно не используемый сервис.

Прочее / кастомные категории:
  Суди ТОЛЬКО по семантике — что конкретно куплено, зачем, насколько это необходимо.
  Числовой порог НЕ применяй."""


def _build_thresholds_context(
    thresholds: dict[str, int] | None,
    custom_categories: list[str] | None = None,
) -> str:
    """Build scenario-aware consciousness guidance for AI prompts.

    Merges user overrides onto defaults. Returns scenario rules + threshold
    table. Thresholds only signal impulsiveness for SINGLE discretionary items.
    """
    merged = dict(DEFAULT_CONSCIOUS_THRESHOLDS)
    if thresholds:
        merged.update(thresholds)  # user overrides win

    threshold_lines = ", ".join(
        f"{cat}: {amt:,} ₸" for cat, amt in sorted(merged.items())
    )
    return (
        _CATEGORY_SCENARIOS
        + "\n\nПороги для единичных дискреционных покупок (₸): "
        + threshold_lines
        + "\nПорог НЕ применяется к оптовым/плановым покупкам (см. сценарии выше)."
    )


@dataclass
class ParsedExpense:
    understood: bool
    amount: float | None
    currency: str
    category: str
    label: str
    confidence: float
    is_conscious: bool | None
    clarification_needed: str | None


@dataclass
class WeeklyInsight:
    text: str


@dataclass
class GeneratedMission:
    title: str
    description: str
    success_criteria: str
    reward_xp: int
    difficulty: str
    why_this_mission: str


@dataclass
class FinancialPersonality:
    archetype: str
    emoji: str
    description: str
    dominant_pattern: str
    growth_hint: str


SYSTEM_PARSE = """You are a financial data extraction engine with expertise in parsing natural language expense descriptions.

Your task: extract structured financial data from a user's free-form text input with maximum accuracy.

Behavioral rules:
- Extract ONLY what is explicitly stated or strongly implied
- For ambiguous inputs, lower confidence score rather than guessing
- Support mixed-language input (Russian + English + slang)
- Recognize common abbreviations: "к" = 1000, "тыс" = 1000, "м" = 1000000
- Infer category from semantic context, not just keywords
- Currency is ALWAYS "KZT" (Kazakhstan Tenge) — ignore any other currency mentions

User's custom categories (prioritize these): {custom_categories}
Default categories if no match: Еда, Транспорт, Развлечения, Здоровье, Одежда, Коммунальные, Подписки, Прочее

{thresholds_context}

Output ONLY valid JSON matching this exact schema:
{{
  "understood": boolean,
  "amount": number | null,
  "currency": "KZT",
  "category": string,
  "label": string (max 40 chars, human-readable, Russian),
  "confidence": number (0.0-1.0),
  "is_conscious": boolean | null,
  "clarification_needed": string | null (question in Russian if confidence < 0.75)
}}

Consciousness (is_conscious) — YOU are the only authority. Apply scenario rules and thresholds above.

Core decision logic:
- true: necessity, regular obligation, bulk/weekly shopping, planned purchase — even at high total amount
- false: single discretionary item exceeding its threshold, impulse treat, lifestyle splurge with no stated plan
- null: genuinely ambiguous — not enough info even after inference

Critical: a large grocery basket (продукты 7000) is CONSCIOUS. One coffee for 3500 is IMPULSIVE.
When torn between null and false for clearly non-essential single items, prefer false."""

SYSTEM_CONSCIOUS_JUDGE = """You are the sole judge of whether one expense was financially conscious (planned / necessary) vs impulsive / discretionary.

You receive structured fields: label, amount in ₸, category, optional extra context (e.g. store). Reason semantically — never use keyword matching rules.

{thresholds_context}

Output ONLY valid JSON: {{"is_conscious": true | false | null}}

Decision logic:
- true: necessity, regular obligation, bulk/planned purchase — even large amounts (e.g. weekly groceries)
- false: single discretionary item over its threshold, impulse treat, lifestyle splurge, one café item at inflated price
- null: not enough information even after inference

Key example: «продукты 7000» = true (bulk grocery run). «кофе 3500» = false (single overpriced treat)."""

SYSTEM_INSIGHT = """You are a behavioral finance coach with deep expertise in spending psychology and habit formation.

Your role: deliver ONE surgical insight that creates a genuine "aha moment" by mirroring the user's behavior back at them — connecting their spending pattern directly to their own stated goal.

MANDATORY insight structure (4 sentences, strictly in this order):
1. Name the TOP 1-2 spending leaks using EXACT figures from the user's data (category + amount in ₸). If a "Top expenses" list is provided, reference at least one real line from it by name and amount.
2. Connect those leaks to the user's goal with a concrete arithmetic link (e.g. "эти X ₸ за 7 дней = Y% от остатка до цели" or "при таком темпе к цели добавится ~Z дней") — use only numbers that follow from the supplied totals.
3. One sentence that contrasts intention vs behavior — specific, not generic moralizing.
4. ONE micro-action for the next 7 days with a measurable number tied to the data (e.g. "не больше N ₸ на кофе", "1 вечер дома вместо кафе", "лимит M ₸ на категорию ___") — never vague "следи за тратами".

Rules:
- Forbidden vague phrases: "сократи расходы", "будь дисциплинирован", "подумай о бюджете" without numbers.
- Use exact numbers from the data, never invent categories or amounts not present in the user message.
- ALWAYS end sentence 4 by referencing the user's goal by name.
- Tone: honest trusted friend, direct but not judgmental
- Language: Russian
- Length: exactly 4 sentences in the insight, no more

User's active goal: {goal}
Goal deadline: {deadline}
Days remaining: {days_remaining}

Output ONLY valid JSON:
{{"insight": "4 sentences in Russian here"}}"""

SYSTEM_MISSION = """You are a behavioral design expert creating personalized financial micro-challenges.

Design principles:
1. Mission must address the user's SINGLE weakest pattern (from data)
2. Must be completable within 7 days with clear success criteria
3. Reward XP proportional to difficulty and impact
4. Frame as an experiment, not a restriction — curiosity over guilt

Difficulty calibration:
- easy (50-80 XP): change one small habit, one day challenge
- medium (100-150 XP): week-long behavioral shift, requires planning
- hard (170-200 XP): significant lifestyle change, high willpower cost

User context:
- Goal: {goal}
- Current level: {level}
- Dominant weakness pattern: {weakness}

Output ONLY valid JSON:
{{
  "title": string (max 50 chars, action-oriented, engaging),
  "description": string (what exactly to do, 1-2 sentences),
  "success_criteria": string (objective, measurable completion condition),
  "reward_xp": number,
  "difficulty": "easy" | "medium" | "hard",
  "why_this_mission": string (one sentence: connect to their specific pattern)
}}

Language: Russian."""

SYSTEM_PERSONALITY = """You are a behavioral economist specializing in financial decision-making archetypes.

Analyze the provided spending data and classify the user into exactly ONE archetype.
Classification must be based on BEHAVIORAL PATTERNS only, not on income level or total spending.

Archetypes (ordered by awareness level):
1. "Импульсивный" — decisions driven by emotion and moment, no pattern awareness
2. "Осознанный" — tracks behavior, has categories, reactive planning
3. "Стратег" — goal-driven decisions, deliberate trade-offs, proactive
4. "Инвестор" — optimizes every decision against long-term opportunity cost

Archetype emojis: Импульсивный=🌊, Осознанный=🧭, Стратег=⚡, Инвестор=🏔️

Output ONLY valid JSON:
{{
  "archetype": string (one of the four above),
  "emoji": string,
  "description": string (2 sentences: specific to THIS user's data, not generic),
  "dominant_pattern": string (the single most characteristic behavior you observed),
  "growth_hint": string (one specific behavior change that moves them to the next level)
}}

Language: Russian."""


SYSTEM_TIP = """You are a sharp, empathetic financial coach. The user just logged an expense.
Analyze it in the context of their recent spending and give ONE short, personalized insight.

Rules:
- Max 2 sentences. Be specific — use numbers from the data.
- Tone: friendly, direct, non-judgmental. Like a smart friend, not a bank.
- Vary your angle: sometimes praise a good pattern, sometimes flag a risk, sometimes give a concrete saving tip.
- NEVER repeat generic advice like "track your spending" or "make a budget".
- Language: Russian.

Output ONLY valid JSON: {{"tip": "string", "type": "praise" | "warning" | "suggestion"}}"""


SYSTEM_RECEIPT = """You are an OCR-based expense parser. The user has sent a photo of a receipt or price tag.
Extract all expense items and return structured data.

Output ONLY valid JSON:
{
  "understood": boolean,
  "items": [{"label": string, "amount": number, "category": string}],
  "total": number | null,
  "currency": "KZT",
  "store_name": string | null
}

Rules:
- Currency is ALWAYS "KZT" regardless of what is printed on the receipt
- If total is present on receipt — use it as the main amount
- category options: Еда, Транспорт, Развлечения, Здоровье, Одежда, Коммунальные, Подписки, Прочее
- If photo is not a receipt/price tag, set understood=false
- Language for labels: Russian"""


class AIService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def _cached_call(self, cache_key: str, messages: list[dict], **kwargs) -> dict[str, Any]:
        """Call OpenAI with Redis cache."""
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)

        response = await self._client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=messages,
            **kwargs,
        )
        result = json.loads(response.choices[0].message.content)
        await redis.set(cache_key, json.dumps(result), ex=CACHE_TTL)
        return result

    async def parse_expense(
        self,
        raw_input: str,
        custom_categories: list[str],
        thresholds: dict[str, int] | None = None,
    ) -> ParsedExpense:
        cache_key = f"parse:v5:{hashlib.md5(raw_input.encode()).hexdigest()}"
        try:
            cats = ", ".join(custom_categories) if custom_categories else "нет"
            thresholds_context = _build_thresholds_context(thresholds, custom_categories)
            data = await self._cached_call(
                cache_key,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PARSE.format(
                            custom_categories=cats,
                            thresholds_context=thresholds_context,
                        ),
                    },
                    {"role": "user", "content": raw_input},
                ],
                temperature=0.1,
            )
            return ParsedExpense(
                understood=data.get("understood", False),
                amount=data.get("amount"),
                currency="KZT",
                category=data.get("category", "Прочее"),
                label=data.get("label", raw_input[:40]),
                confidence=data.get("confidence", 0.0),
                is_conscious=data.get("is_conscious"),
                clarification_needed=data.get("clarification_needed"),
            )
        except Exception as e:
            logger.error("parse_expense_failed", error=str(e), raw_input=raw_input)
            raise

    async def judge_expense_consciousness(
        self,
        *,
        label: str,
        amount: float,
        category: str,
        extra_context: str | None = None,
        thresholds: dict[str, int] | None = None,
    ) -> bool | None:
        """Second-pass AI judgment (uncached) — receipts / null from parse / manual web edits."""
        thresholds_context = _build_thresholds_context(thresholds)
        user_lines = [
            f"Категория: {category}",
            f"Сумма: {amount:,.0f} ₸",
            f"Название: {label}",
        ]
        if extra_context:
            user_lines.append(f"Контекст: {extra_context}")
        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_CONSCIOUS_JUDGE.format(
                            thresholds_context=thresholds_context,
                        ),
                    },
                    {"role": "user", "content": "\n".join(user_lines)},
                ],
                temperature=0.2,
                max_tokens=80,
            )
            data = json.loads(response.choices[0].message.content)
            v = data.get("is_conscious")
            if v is True:
                return True
            if v is False:
                return False
            return None
        except Exception as e:
            logger.error("judge_consciousness_failed", error=str(e))
            return None

    async def generate_weekly_insight(
        self,
        expenses_summary: str,
        goal: str,
        deadline: str,
        days_remaining: int,
    ) -> WeeklyInsight:
        try:
            data = await self._cached_call(
                f"insight:{hashlib.md5(expenses_summary.encode()).hexdigest()}",
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_INSIGHT.format(
                            goal=goal,
                            deadline=deadline,
                            days_remaining=days_remaining,
                        ),
                    },
                    {"role": "user", "content": expenses_summary},
                ],
                temperature=0.7,
            )
            return WeeklyInsight(text=data.get("insight", data.get("text", "")))
        except Exception as e:
            logger.error("generate_weekly_insight_failed", error=str(e))
            raise

    async def generate_mission(
        self,
        goal: str,
        level: int,
        weakness: str,
    ) -> GeneratedMission:
        try:
            data = await self._cached_call(
                f"mission:{hashlib.md5(f'{goal}{level}{weakness}'.encode()).hexdigest()}",
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_MISSION.format(goal=goal, level=level, weakness=weakness),
                    },
                    {"role": "user", "content": "Сгенерируй миссию."},
                ],
                temperature=0.8,
            )
            return GeneratedMission(
                title=data["title"],
                description=data["description"],
                success_criteria=data["success_criteria"],
                reward_xp=data["reward_xp"],
                difficulty=data["difficulty"],
                why_this_mission=data["why_this_mission"],
            )
        except Exception as e:
            logger.error("generate_mission_failed", error=str(e))
            raise

    async def transcribe_voice(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """Transcribe voice message via Whisper API. Returns plain text."""
        import io
        try:
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=(filename, io.BytesIO(audio_bytes), "audio/ogg"),
                language="ru",
            )
            return response.text.strip()
        except Exception as e:
            logger.error("transcribe_voice_failed", error=str(e))
            raise

    async def parse_receipt_image(self, image_bytes: bytes) -> dict:
        """Parse receipt/price tag photo via GPT-4o vision. Returns raw dict."""
        import base64
        try:
            b64 = base64.b64encode(image_bytes).decode()
            response = await self._client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_RECEIPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": "Распарси этот чек/ценник."},
                    ]},
                ],
                max_tokens=600,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error("parse_receipt_failed", error=str(e))
            raise

    async def generate_spending_tip(self, expense_label: str, amount: float, category: str, recent_summary: str) -> dict:
        """Generate a short personalized tip after an expense. Returns {tip, type}."""
        user_msg = (
            f"Только что записал: {expense_label} — {amount:,.0f} ₸ ({category}).\n\n"
            f"Последние траты:\n{recent_summary}"
        )
        try:
            # Don't cache tips — they should be fresh each time
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_TIP},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.9,
                max_tokens=150,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error("generate_tip_failed", error=str(e))
            return {}

    async def evaluate_financial_personality(self, expenses_data: str) -> FinancialPersonality:
        try:
            data = await self._cached_call(
                f"personality:{hashlib.md5(expenses_data.encode()).hexdigest()}",
                messages=[
                    {"role": "system", "content": SYSTEM_PERSONALITY},
                    {"role": "user", "content": expenses_data},
                ],
                temperature=0.3,
            )
            return FinancialPersonality(
                archetype=data["archetype"],
                emoji=data["emoji"],
                description=data["description"],
                dominant_pattern=data["dominant_pattern"],
                growth_hint=data["growth_hint"],
            )
        except Exception as e:
            logger.error("evaluate_personality_failed", error=str(e))
            raise


ai_service = AIService()
