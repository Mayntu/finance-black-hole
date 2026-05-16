from datetime import datetime, timezone

import structlog
from sqlalchemy import func as sqlfunc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.expense import Expense
from models.goal import Goal
from models.user import User

logger = structlog.get_logger(__name__)


class ExpenseService:
    async def create_expense(
        self,
        session: AsyncSession,
        user: User,
        raw_input: str,
        amount: float,
        category: str,
        label: str,
        ai_confidence: float,
        is_conscious: bool | None,
    ) -> Expense:
        # Find active goal
        active_goal = await self._get_active_goal(session, user.id)

        expense = Expense(
            user_id=user.id,
            raw_input=raw_input,
            amount=amount,
            category=category,
            label=label,
            ai_confidence=ai_confidence,
            is_conscious=is_conscious,
            goal_id=active_goal.id if active_goal else None,
        )
        session.add(expense)
        await session.flush()
        logger.info("expense_created", user_id=user.id, amount=amount, category=category)
        return expense

    async def _get_active_goal(self, session: AsyncSession, user_id: int) -> Goal | None:
        result = await session.execute(
            select(Goal).where(Goal.user_id == user_id, Goal.is_active == True, Goal.is_completed == False)
            .order_by(Goal.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_expense_count(self, session: AsyncSession, user_id: int) -> int:
        result = await session.execute(
            select(sqlfunc.count(Expense.id)).where(Expense.user_id == user_id)
        )
        return result.scalar_one()

    async def add_savings_to_goal(
        self,
        session: AsyncSession,
        user_id: int,
        amount: float,
        goal_id: int | None = None,
    ) -> tuple["Goal | None", bool]:
        """
        Add `amount` to the user's active goal.
        Returns (goal, just_completed).
        """
        if goal_id:
            result = await session.execute(
                select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
            )
            goal = result.scalar_one_or_none()
        else:
            goal = await self._get_active_goal(session, user_id)

        if not goal:
            return None, False

        goal.current_amount += amount
        just_completed = (
            goal.current_amount >= goal.target_amount and not goal.is_completed
        )
        if just_completed:
            goal.is_completed = True
            goal.is_active = False
            goal.completed_at = datetime.now(timezone.utc)

        session.add(goal)
        await session.flush()
        logger.info(
            "savings_added",
            user_id=user_id,
            amount=amount,
            goal_id=goal.id,
            completed=just_completed,
        )
        return goal, just_completed


expense_service = ExpenseService()
