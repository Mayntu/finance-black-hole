from celery import Celery
from celery.schedules import crontab

from core.config import settings

celery_app = Celery(
    "financeblackhole",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "tasks.reminders",
        "tasks.weekly_insight",
        "tasks.daily_mission",
        "tasks.personality_update",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Almaty",
    enable_utc=True,
    beat_schedule={
        # Reminder at 20:00 if no expense logged today
        "daily-reminder": {
            "task": "tasks.reminders.send_daily_reminders",
            "schedule": crontab(hour=20, minute=0),
        },
        # Weekly insight every Monday at 9:00
        "weekly-insight": {
            "task": "tasks.weekly_insight.send_weekly_insights",
            "schedule": crontab(hour=9, minute=0, day_of_week=1),
        },
        # Weekly missions every Sunday at 18:00
        "weekly-mission": {
            "task": "tasks.daily_mission.generate_weekly_missions",
            "schedule": crontab(hour=18, minute=0, day_of_week=0),
        },
        # Personality batch recalculation every Sunday at 02:00
        "personality-update": {
            "task": "tasks.personality_update.run_personality_updates",
            "schedule": crontab(hour=2, minute=0, day_of_week=0),
        },
    },
)
