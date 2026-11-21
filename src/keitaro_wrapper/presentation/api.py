from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from keitaro_wrapper.application.services import CampaignService
from keitaro_wrapper.domain.models import (
    CampaignCommand,
    ConfigurationError,
    IntegrationError,
    Operation,
    RequestConflict,
    normalize_country,
    normalize_name,
)


class CampaignInput(BaseModel):
    """Проверяет ввод до начала внешних изменений."""

    request_id: UUID
    name: str = Field(min_length=1, max_length=128)
    country_code: str
    offer_id: int = Field(gt=0)

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """Добавляет обязательный префикс и проверяет длину результата."""
        return normalize_name(value)

    @field_validator('country_code')
    @classmethod
    def normalize_country(cls, value: str) -> str:
        """Принимает существующий двухбуквенный код страны ISO 3166-1."""
        return normalize_country(value)


class OperationOutput(BaseModel):
    """Описывает состояние операции и известные ресурсы Keitaro."""

    id: str
    name: str
    country_code: str
    offer_id: int
    created_at: str
    status: str
    campaign_id: int | None
    google_stream_id: int | None
    offer_stream_id: int | None
    url: str | None
    error: str | None


def serialize(operation: Operation) -> dict[str, object]:
    """Готовит доменную операцию к JSON-ответу."""
    result: dict[str, object] = asdict(operation)
    result['created_at'] = operation.created_at.isoformat()
    return result


def make_router(templates: Jinja2Templates) -> APIRouter:
    """Создаёт HTTP-слой; инфраструктуру предоставляет точка сборки."""
    router: APIRouter = APIRouter()

    @router.get('/', response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        """Показывает форму создания кампании."""
        return templates.TemplateResponse(request=request, name='index.html')

    @router.get('/health')
    def health() -> dict[str, str]:
        """Подтверждает доступность процесса приложения."""
        return {'status': 'ok'}

    @router.get('/api/catalog')
    def catalog(request: Request) -> dict[str, object]:
        """Загружает офферы и настройки без изменения Keitaro."""
        with request.app.state.services() as service:
            service: CampaignService
            try:
                return service.catalog()
            except ConfigurationError as error:
                raise HTTPException(503, str(error)) from None
            except IntegrationError as error:
                raise HTTPException(502, str(error)) from None

    @router.get('/api/operations', response_model=list[OperationOutput])
    def operations(request: Request) -> list[dict[str, object]]:
        """Показывает журнал, включая частичные ошибки."""
        with request.app.state.services() as service:
            service: CampaignService
            return [serialize(operation) for operation in service.recent()]

    @router.post('/api/campaigns', response_model=OperationOutput)
    def create_campaign(data: CampaignInput, request: Request) -> dict[str, object]:
        """Запускает идемпотентное создание кампании и двух потоков."""
        with request.app.state.services() as service:
            service: CampaignService
            try:
                operation: Operation = service.create(
                    CampaignCommand(
                        request_id=str(data.request_id),
                        name=data.name,
                        country_code=data.country_code,
                        offer_id=data.offer_id,
                    )
                )
            except RequestConflict as error:
                raise HTTPException(409, str(error)) from None
            return serialize(operation)

    return router
