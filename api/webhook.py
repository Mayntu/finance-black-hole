import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request

from core.config import settings

logger = structlog.get_logger(__name__)
router = APIRouter()

_bot: Bot | None = None
_dp: Dispatcher | None = None


def set_bot_dp(bot: Bot, dp: Dispatcher) -> None:
    global _bot, _dp
    _bot = bot
    _dp = dp


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> dict:
    if x_telegram_bot_api_secret_token != settings.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    if _bot is None or _dp is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    data = await request.json()
    update = Update.model_validate(data)
    await _dp.feed_update(_bot, update)
    return {"ok": True}
