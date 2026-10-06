import random
import string
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.images import render_passport_main, render_passport_registration, render_selfie_passport
from app.schemas import (
    ApplicationDetail,
    ApplicationForm,
    ApplicationSummary,
    Comment,
    ConsentInfo,
    CreateApplicationRequest,
    PartnerStatus,
    StageBFields,
    StatusHistoryEntry,
    TechnicalData,
)

# Допустимые переходы статуса в системе заказчика.
ALLOWED_TRANSITIONS: dict[PartnerStatus, set[PartnerStatus]] = {
    PartnerStatus.NEW: {PartnerStatus.ON_CALL, PartnerStatus.REJECTED},
    PartnerStatus.ON_CALL: {PartnerStatus.ON_REVIEW, PartnerStatus.REJECTED},
    PartnerStatus.ON_REVIEW: {
        PartnerStatus.ON_CALL,
        PartnerStatus.REJECTED,
        PartnerStatus.MANUAL_DECISION,
        PartnerStatus.APPROVED,
    },
    PartnerStatus.MANUAL_DECISION: {PartnerStatus.REJECTED, PartnerStatus.APPROVED},
    PartnerStatus.REJECTED: set(),
    PartnerStatus.APPROVED: set(),
}

_SAMPLE_NAMES = [
    ("Иванов", "Иван", "Иванович"),
    ("Петрова", "Мария", "Сергеевна"),
    ("Сидоров", "Алексей", "Николаевич"),
    ("Кузнецова", "Ольга", "Дмитриевна"),
    ("Смирнов", "Дмитрий", "Андреевич"),
    ("Попова", "Наталья", "Викторовна"),
    ("Васильев", "Сергей", "Петрович"),
    ("Новикова", "Екатерина", "Игоревна"),
    ("Морозов", "Артём", "Олегович"),
    ("Волкова", "Анна", "Алексеевна"),
]
_SAMPLE_GOODS = [
    "Стиральная машина",
    "Холодильник",
    "Телевизор",
    "Ноутбук",
    "Диван",
    "Кухонный гарнитур",
    "Смартфон",
    "Велосипед",
    "Кондиционер",
    "Швейная машина",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _random_passport_number() -> tuple[str, str]:
    series = "".join(random.choices(string.digits, k=4))
    number = "".join(random.choices(string.digits, k=6))
    return series, number


def _new_run_prefix() -> str:
    """Случайный префикс external_id на запуск/сброс заглушки (uuid, 8 символов)."""
    return uuid.uuid4().hex[:8]


@dataclass
class _StoredApplication:
    id: str
    status: PartnerStatus
    created_at: datetime
    updated_at: datetime
    form: ApplicationForm
    technical: TechnicalData
    passport_fields: dict
    stage_b: StageBFields | None = None
    comments: list[Comment] = field(default_factory=list)
    status_history: list[StatusHistoryEntry] = field(default_factory=list)


class PartnerStore:
    def __init__(self) -> None:
        self._applications: dict[str, _StoredApplication] = {}
        self._counter = 0
        # Случайный префикс на запуск процесса: store — только в памяти, без
        # volume, и при любом перезапуске (docker compose build/up, падение
        # контейнера) счётчик обнуляется. Заказчик (app/db, том db_data)
        # переживает перезапуск заглушки — без префикса новые заявки после
        # рестарта получали бы те же external_id, что уже заняты вчерашними
        # обработанными заявками в БД заказчика, и sync принимал бы это за
        # попытку переписать статус, который "оператор уже изменил вручную"
        # (правило 12 CLAUDE.md) — ложный conflict вместо обработки новой заявки.
        self._run_prefix = _new_run_prefix()

    def _next_id(self) -> str:
        self._counter += 1
        return f"APP-{self._run_prefix}-{self._counter:06d}"

    def create_application(self, payload: CreateApplicationRequest) -> _StoredApplication:
        now = _now()
        series, number = _random_passport_number()
        birth_date = payload.birth_date or date(1985, 6, 15)

        form = ApplicationForm(
            last_name=payload.last_name,
            first_name=payload.first_name,
            middle_name=payload.middle_name,
            phone=payload.phone,
            goods_name=payload.goods_name,
            merchant_name=payload.merchant_name,
            merchant_city=payload.merchant_city,
            goods_price=payload.goods_price,
            down_payment=payload.down_payment,
            term_months=payload.term_months,
            tariff=payload.tariff,
            consent_pd=ConsentInfo(given_at=now, ip="127.0.0.1", text_version="v1"),
            consent_bki=(
                ConsentInfo(given_at=now, ip="127.0.0.1", text_version="v1")
                if payload.consent_bki
                else None
            ),
        )

        passport_fields = {
            "last_name": payload.last_name,
            "first_name": payload.first_name,
            "middle_name": payload.middle_name,
            "birth_date": birth_date.isoformat(),
            "gender": "female" if payload.first_name.endswith(("а", "я")) else "male",
            "passport_series": series,
            "passport_number": number,
            "issue_date": date(2015, 3, 10).isoformat(),
            "issued_by": "УМВД России по тестовому региону",
            "department_code": "770-001",
            "registration_address": "г. Тестоград, ул. Примерная, д. 1, кв. 1",
        }

        application = _StoredApplication(
            id=self._next_id(),
            status=PartnerStatus.NEW,
            created_at=now,
            updated_at=now,
            form=form,
            technical=TechnicalData(ip="127.0.0.1", user_agent="fake-partner-seed"),
            passport_fields=passport_fields,
        )
        application.status_history.append(
            StatusHistoryEntry(from_status=None, to_status=PartnerStatus.NEW, changed_by="system", changed_at=now)
        )
        self._applications[application.id] = application
        return application

    def reset(self, seed_count: int = 10) -> None:
        """Только для тестов (см. POST /_test/reset) — очищает накопленные за
        время работы процесса заявки. list_applications ниже — O(N) по ВСЕМ
        когда-либо созданным заявкам (сканирует self._applications целиком,
        не только совпадающие по статусу), а store никогда не уменьшается
        сам по себе: без сброса между тестами N растёт на каждый пересоздание
        pytest-сессии в рамках одного и того же поднятого docker compose,
        и sync_applications (вызывается почти в каждом интеграционном тесте)
        платит за этот скан дважды на тест. Отсюда и деградация полного
        прогона от ~20 секунд до нескольких минут по мере повторных запусков
        pytest без перезапуска сервиса."""
        self._applications.clear()
        self._counter = 0
        # Новый префикс на каждый сброс: без него после reset счётчик снова
        # дал бы external_id, уже занятые заявками из прошлых прогонов в БД
        # заказчика, и приём (идемпотентный по external_id) молча пропускал бы
        # новые заявки — e2e_check падал без понятной причины.
        self._run_prefix = _new_run_prefix()
        self.seed(seed_count)

    def seed(self, count: int = 10) -> None:
        for i in range(count):
            last, first, middle = _SAMPLE_NAMES[i % len(_SAMPLE_NAMES)]
            goods = _SAMPLE_GOODS[i % len(_SAMPLE_GOODS)]
            self.create_application(
                CreateApplicationRequest(
                    last_name=last,
                    first_name=first,
                    middle_name=middle,
                    phone=f"+7999000{i:04d}",
                    goods_name=goods,
                    goods_price=30000.0 + i * 5000,
                    down_payment=3000.0 + i * 500,
                    term_months=6 + (i % 4) * 3,
                )
            )

    def list_applications(
        self, status: PartnerStatus | None = None, since: datetime | None = None
    ) -> list[_StoredApplication]:
        items = list(self._applications.values())
        if status is not None:
            items = [a for a in items if a.status == status]
        if since is not None:
            items = [a for a in items if a.updated_at >= since]
        return sorted(items, key=lambda a: a.created_at)

    def get(self, application_id: str) -> _StoredApplication | None:
        return self._applications.get(application_id)

    def get_photo(self, application_id: str, kind: str) -> bytes | None:
        application = self.get(application_id)
        if application is None:
            return None
        if kind == "passport_main":
            return render_passport_main(application.passport_fields)
        if kind == "passport_reg":
            return render_passport_registration(application.passport_fields)
        if kind == "selfie_passport":
            return render_selfie_passport()
        return None

    def change_status(
        self, application_id: str, new_status: PartnerStatus, changed_by: str, comment: str | None = None
    ) -> _StoredApplication | None:
        application = self.get(application_id)
        if application is None:
            return None

        allowed = ALLOWED_TRANSITIONS.get(application.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"transition {application.status} -> {new_status} is not allowed"
            )

        now = _now()
        application.status_history.append(
            StatusHistoryEntry(
                from_status=application.status,
                to_status=new_status,
                changed_by=changed_by,
                changed_at=now,
            )
        )
        application.status = new_status
        application.updated_at = now
        if comment:
            application.comments.append(Comment(text=comment, created_at=now))
        return application

    def force_set_status(
        self, application_id: str, new_status: PartnerStatus, changed_by: str, reason: str | None = None
    ) -> _StoredApplication | None:
        """Меняет статус без проверки допустимости перехода — имитирует ручное
        изменение статуса оператором в интерфейсе заказчика (см. правило 12 CLAUDE.md)."""
        application = self.get(application_id)
        if application is None:
            return None

        now = _now()
        application.status_history.append(
            StatusHistoryEntry(
                from_status=application.status,
                to_status=new_status,
                changed_by=changed_by,
                changed_at=now,
                reason=reason,
            )
        )
        application.status = new_status
        application.updated_at = now
        return application

    def add_comment(self, application_id: str, text: str) -> _StoredApplication | None:
        application = self.get(application_id)
        if application is None:
            return None
        application.comments.append(Comment(text=text, created_at=_now()))
        application.updated_at = _now()
        return application

    def set_stage_b_fields(self, application_id: str, fields: StageBFields) -> _StoredApplication | None:
        application = self.get(application_id)
        if application is None:
            return None
        application.stage_b = fields
        application.updated_at = _now()
        return application

    @staticmethod
    def to_summary(application: _StoredApplication) -> ApplicationSummary:
        return ApplicationSummary(
            id=application.id,
            status=application.status,
            created_at=application.created_at,
            updated_at=application.updated_at,
            last_name=application.form.last_name,
            first_name=application.form.first_name,
            goods_name=application.form.goods_name,
        )

    @staticmethod
    def to_detail(application: _StoredApplication) -> ApplicationDetail:
        return ApplicationDetail(
            id=application.id,
            status=application.status,
            created_at=application.created_at,
            updated_at=application.updated_at,
            last_name=application.form.last_name,
            first_name=application.form.first_name,
            goods_name=application.form.goods_name,
            form=application.form,
            technical=application.technical,
            photos=["selfie_passport", "passport_main", "passport_reg"],
            stage_b=application.stage_b,
            comments=application.comments,
            status_history=application.status_history,
        )


store = PartnerStore()
