"""Telegram Mini App initData validation and token issuance."""
import hashlib
import hmac
import json
from urllib.parse import unquote, parse_qs

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.auth import create_dashboard_token
from core.config import settings
from models.user import User

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/webapp", tags=["webapp"])


class InitDataRequest(BaseModel):
    init_data: str  # raw Telegram.WebApp.initData string


class TokenResponse(BaseModel):
    token: str
    user_id: int
    full_name: str


def _validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Validate Telegram WebApp initData HMAC.
    Returns parsed user dict or None if invalid.
    Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    parsed = parse_qs(init_data, keep_blank_values=True)
    hash_value = parsed.pop("hash", [None])[0]
    if not hash_value:
        return None

    # Rebuild data-check-string (sorted key=value pairs, \n-separated)
    data_check = "\n".join(
        f"{k}={v[0]}" for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, hash_value):
        return None

    # Extract user object
    user_raw = parsed.get("user", [None])[0]
    if not user_raw:
        return None
    try:
        return json.loads(unquote(user_raw))
    except Exception:
        return None


@router.post("/auth", response_model=TokenResponse)
async def webapp_auth(
    body: InitDataRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Called from the Mini App on first load.
    Validates Telegram initData, returns a JWT for the dashboard.
    """
    user_data = _validate_init_data(body.init_data, settings.BOT_TOKEN)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid initData")

    telegram_id = user_data.get("id")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="No user id in initData")

    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not registered. Start the bot first.")

    token = create_dashboard_token(telegram_id)
    logger.info("webapp_auth_ok", telegram_id=telegram_id)
    return TokenResponse(token=token, user_id=telegram_id, full_name=user.full_name)
