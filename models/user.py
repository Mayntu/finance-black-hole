from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base

if TYPE_CHECKING:
    from models.expense import Expense
    from models.goal import Goal
    from models.mission import Mission
    from models.achievement import Achievement


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Financial profile
    currency: Mapped[str] = mapped_column(String(8), default="KZT", server_default="KZT")
    custom_categories: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    monthly_budget: Mapped[float | None] = mapped_column(nullable=True)
    conscious_thresholds: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Gamification
    xp: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    level: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    streak_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    financial_personality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_personality_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    personality_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relations
    expenses: Mapped[list["Expense"]] = relationship("Expense", back_populates="user", lazy="select")
    goals: Mapped[list["Goal"]] = relationship("Goal", back_populates="user", lazy="select")
    missions: Mapped[list["Mission"]] = relationship("Mission", back_populates="user", lazy="select")
    achievements: Mapped[list["Achievement"]] = relationship("Achievement", back_populates="user", lazy="select")
