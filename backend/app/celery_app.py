from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "dialog_scoring",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.integration",
        "app.tasks.stage_a",
        "app.tasks.stage_b",
        "app.tasks.registries",
        "app.tasks.fssp_poll",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "sync-partner-applications": {
            "task": "integration.sync_applications",
            "schedule": settings.partner_poll_interval_seconds,
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
        "poll-fssp": {
            "task": "fssp.poll_pending",
            "schedule": settings.fssp_poll_interval_seconds,
        },
    },
)
