from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    waiting_goal = State()
    waiting_goal_amount = State()
    waiting_categories = State()


class GoalStates(StatesGroup):
    waiting_title = State()
    waiting_amount = State()
    waiting_deadline = State()


class ClarifyExpenseState(StatesGroup):
    waiting_clarification = State()
    waiting_amount = State()        # amount=null case
    waiting_edit_text = State()     # user types new description for edit
    waiting_receipt_confirmation = State()  # receipt photo parsed, waiting confirm


class CategoryStates(StatesGroup):
    waiting_new_category = State()
