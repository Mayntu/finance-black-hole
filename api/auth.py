from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.auth import create_dashboard_token, decode_dashboard_token  # re-export
from models.user import User

__all__ = ["create_dashboard_token", "decode_dashboard_token", "get_current_user"]


async def get_current_user(
    token: str = Query(..., description="JWT token from /dashboard bot command"),
    session: AsyncSession = Depends(get_db),
) -> User:
    telegram_id = decode_dashboard_token(token)
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
