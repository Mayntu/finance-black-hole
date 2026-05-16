"""JWT helpers — shared between bot and API."""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from core.config import settings


def create_dashboard_token(telegram_id: int) -> str:
    payload = {
        "telegram_id": telegram_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.JWT_TTL_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_dashboard_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("telegram_id")
    except JWTError:
        return None
