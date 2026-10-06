"""ФНС — запуск фоновой проверки, не блокирующей стадию А/Б (T16, ваше явное
указание). Федресурс и старый синхронный протокол ФССП (submit/poll,
api-ip.fssp.gov.ru, закрыт с 2022) убраны целиком — ФССП теперь только по
действию оператора через app/api/fssp.py, см. app/sources/fssp.py."""

from uuid import UUID

from app.sources.base import Subject


def _subject_to_payload(subject: Subject) -> dict:
    return {
        "last_name": subject.last_name,
        "first_name": subject.first_name,
        "middle_name": subject.middle_name,
        "birth_date": subject.birth_date.isoformat() if subject.birth_date else None,
        "passport_series": subject.passport_series,
        "passport_number": subject.passport_number,
        "passport_issue_date": subject.passport_issue_date.isoformat() if subject.passport_issue_date else None,
    }


def queue_fns_checks(
    *, application_id: UUID, subject_type: str, subject_id: UUID | None, subject: Subject,
) -> None:
    """.delay() — не await: ставит задачу в очередь Celery и возвращается
    немедленно, результат появится в checks когда появится (ваше явное
    указание — стадия А/Б не ждёт). Импорт таска — внутри функции, не на
    уровне модуля: app.tasks.fns_checks импортирует app.celery_app, а он
    проверяет окружение при импорте (startup_guard) — не нужно тащить это в
    app.engine, который используют и обычные API-процессы без Celery."""
    from app.tasks.fns_checks import run_fns_checks_task

    run_fns_checks_task.delay(
        str(application_id), subject_type, str(subject_id) if subject_id else None, _subject_to_payload(subject),
    )
