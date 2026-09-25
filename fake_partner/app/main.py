from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Response, status

from app.auth import require_service_account
from app.schemas import (
    ApplicationDetail,
    ApplicationSummary,
    CommentRequest,
    CreateApplicationRequest,
    PartnerStatus,
    StageBFields,
    StatusChangeRequest,
)
from app.store import store

@asynccontextmanager
async def lifespan(app: FastAPI):
    store.seed(10)
    yield


app = FastAPI(title="fake_partner — заглушка системы заказчика", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _parse_status(raw: str) -> PartnerStatus:
    try:
        return PartnerStatus(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown status: {raw!r}, allowed: {[s.value for s in PartnerStatus]}",
        )


# --- «Что нам нужно от их API» -------------------------------------------------


@app.get(
    "/applications",
    response_model=list[ApplicationSummary],
    dependencies=[Depends(require_service_account)],
)
def list_applications(status_: str | None = None, since: datetime | None = None):
    parsed_status = _parse_status(status_) if status_ is not None else None
    items = store.list_applications(status=parsed_status, since=since)
    return [store.to_summary(a) for a in items]


@app.get(
    "/applications/{application_id}",
    response_model=ApplicationDetail,
    dependencies=[Depends(require_service_account)],
)
def get_application(application_id: str):
    application = store.get(application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")
    return store.to_detail(application)


@app.get(
    "/applications/{application_id}/photos/{kind}",
    dependencies=[Depends(require_service_account)],
)
def get_photo(application_id: str, kind: str):
    if store.get(application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    photo = store.get_photo(application_id, kind)
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    return Response(content=photo, media_type="image/png")


@app.post(
    "/applications/{application_id}/status",
    response_model=ApplicationDetail,
    dependencies=[Depends(require_service_account)],
)
def change_status(application_id: str, payload: StatusChangeRequest):
    if store.get(application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    new_status = _parse_status(payload.status)
    try:
        application = store.change_status(
            application_id, new_status, changed_by="system", comment=payload.comment
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return store.to_detail(application)


@app.post(
    "/applications/{application_id}/comments",
    response_model=ApplicationDetail,
    dependencies=[Depends(require_service_account)],
)
def add_comment(application_id: str, payload: CommentRequest):
    application = store.add_comment(application_id, payload.text)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")
    return store.to_detail(application)


# --- Служебные эндпоинты для тестов --------------------------------------------


@app.post(
    "/_test/applications",
    response_model=ApplicationDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_service_account)],
)
def test_create_application(payload: CreateApplicationRequest):
    application = store.create_application(payload)
    return store.to_detail(application)


@app.post(
    "/_test/applications/{application_id}/review",
    response_model=ApplicationDetail,
    dependencies=[Depends(require_service_account)],
)
def test_press_review(application_id: str):
    """Имитирует нажатие кнопки «Рассмотреть» оператором после звонка."""
    if store.get(application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")
    try:
        application = store.change_status(
            application_id, PartnerStatus.ON_REVIEW, changed_by="operator"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return store.to_detail(application)


@app.post(
    "/_test/applications/{application_id}/stage-b-fields",
    response_model=ApplicationDetail,
    dependencies=[Depends(require_service_account)],
)
def test_fill_stage_b_fields(application_id: str, payload: StageBFields):
    application = store.set_stage_b_fields(application_id, payload)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")
    return store.to_detail(application)


@app.post(
    "/_test/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_service_account)],
)
def test_reset():
    """Очищает накопленные заявки между тестами (см. store.reset) — без
    этого store растёт неограниченно на весь срок жизни контейнера и
    list_applications (O(N) по всем когда-либо созданным заявкам) заметно
    замедляет sync_applications по мере повторных прогонов pytest."""
    store.reset()


@app.post(
    "/_test/applications/{application_id}/manual-status",
    response_model=ApplicationDetail,
    dependencies=[Depends(require_service_account)],
)
def test_manual_status_change(application_id: str, payload: StatusChangeRequest):
    """Имитирует ситуацию, когда оператор вручную сменил статус в системе
    заказчика в обход нашего сервиса (для проверки правила 12 из CLAUDE.md)."""
    if store.get(application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    new_status = _parse_status(payload.status)
    application = store.force_set_status(
        application_id, new_status, changed_by="operator", reason=payload.reason
    )
    return store.to_detail(application)
