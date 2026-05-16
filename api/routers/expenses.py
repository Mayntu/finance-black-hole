from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.deps import get_db
from models.expense import Expense
from models.user import User
from services.ai_service import ai_service

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


class ExpensePatchBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    amount: float = Field(..., gt=0, le=999_999_999)
    category: str = Field(..., min_length=1, max_length=64)


@router.delete("/record/{expense_id}")
async def delete_expense_record(
    expense_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    result = await session.execute(
        select(Expense).where(Expense.id == expense_id, Expense.user_id == user.id)
    )
    exp = result.scalar_one_or_none()
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")
    await session.delete(exp)
    await session.commit()
    return {"ok": True, "id": expense_id}


@router.patch("/record/{expense_id}")
async def patch_expense_record(
    expense_id: int,
    body: ExpensePatchBody,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    result = await session.execute(
        select(Expense).where(Expense.id == expense_id, Expense.user_id == user.id)
    )
    exp = result.scalar_one_or_none()
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")

    exp.label = body.label[:128]
    exp.amount = float(body.amount)
    exp.category = body.category[:64]
    exp.raw_input = f"{body.label} {body.amount:g}"
    try:
        exp.is_conscious = await ai_service.judge_expense_consciousness(
            label=body.label,
            amount=float(body.amount),
            category=body.category,
            extra_context=None,
            thresholds=user.conscious_thresholds or None,
        )
    except Exception:
        exp.is_conscious = None
    exp.ai_confidence = 0.88
    await session.commit()
    await session.refresh(exp)
    return {
        "id": exp.id,
        "label": exp.label,
        "amount": exp.amount,
        "category": exp.category,
        "is_conscious": exp.is_conscious,
        "created_at": exp.created_at.isoformat(),
    }


@router.get("/{telegram_id}")
async def list_expenses(
    telegram_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    category: str | None = Query(None),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    conscious_only: bool | None = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Expense).where(Expense.user_id == user.id)

    if category:
        q = q.where(Expense.category == category)
    if from_date:
        q = q.where(Expense.created_at >= datetime.fromisoformat(from_date))
    if to_date:
        q = q.where(Expense.created_at <= datetime.fromisoformat(to_date))
    if conscious_only is not None:
        q = q.where(Expense.is_conscious == conscious_only)

    # Total count
    from sqlalchemy import func
    count_result = await session.execute(
        select(func.count()).select_from(q.subquery())
    )
    total = count_result.scalar_one()

    # Paginated results
    q = q.order_by(Expense.created_at.desc()).offset((page - 1) * limit).limit(limit)
    result = await session.execute(q)
    expenses = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "items": [
            {
                "id": e.id,
                "raw_input": e.raw_input,
                "label": e.label,
                "amount": e.amount,
                "category": e.category,
                "is_conscious": e.is_conscious,
                "ai_confidence": e.ai_confidence,
                "created_at": e.created_at.isoformat(),
            }
            for e in expenses
        ],
    }
