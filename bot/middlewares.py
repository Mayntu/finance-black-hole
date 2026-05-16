from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


class RateLimitMiddleware(BaseMiddleware):
    """Simple per-user rate limiter: max 10 AI requests/minute."""

    def __init__(self, redis_client) -> None:
        self.redis = redis_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and event.message and event.message.from_user:
            user_id = event.message.from_user.id
            key = f"rate:{user_id}"
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, 60)
            if count > 10:
                if event.message:
                    await event.message.answer(
                        "⏳ Слишком много запросов. Подожди минуту."
                    )
                return None
        return await handler(event, data)
