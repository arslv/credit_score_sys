from celery import Celery

from app.config import get_settings
from app.startup_guard import assert_safe_for_environment

settings = get_settings()
assert_safe_for_environment(settings)

celery_app = Celery(
    "dialog_scoring",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.partner_outbox",
        "app.tasks.stage_a",
        "app.tasks.stage_b",
        "app.tasks.registries",
        "app.tasks.fns_checks",
        "app.tasks.delinquency",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "drain-partner-outbox": {
            "task": "partner_outbox.drain",
            "schedule": settings.partner_outbox_sweep_interval_seconds,
        },
        "process-stage-a": {
            "task": "stage_a.process_new_applications",
            "schedule": settings.stage_a_poll_interval_seconds,
        },
        "process-stage-b": {
            "task": "stage_b.process_reviewed_applications",
            "schedule": settings.stage_a_poll_interval_seconds,  # тот же темп, что стадия А
        },
        "sync-registries": {
            "task": "registries.sync_all",
            "schedule": settings.registries_sync_interval_seconds,
        },
        "recompute-delinquency": {
            "task": "delinquency.recompute_daily",
            "schedule": settings.delinquency_recompute_interval_seconds,
        },
    },
)
