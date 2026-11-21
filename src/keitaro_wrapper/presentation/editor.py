"""Предоставляет HTTP-интерфейс редактора кампаний."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from keitaro_wrapper.application.editor import EditorService
from keitaro_wrapper.domain.models import IntegrationError, RequestConflict


class EditInput(BaseModel):
    """Проверяет команду и ревизию черновика."""

    revision: int = Field(ge=1)
    action: Literal[
        'add', 'restore', 'remove', 'pin', 'share', 'push', 'cancel', 'recover', 'reset'
    ]
    offer_id: int | None = Field(default=None, gt=0)
    share: int | None = Field(default=None, ge=0, le=100, strict=True)


def make_editor_router(templates: Jinja2Templates) -> APIRouter:
    """Создаёт маршруты, делегирующие работу прикладному сервису."""
    router: APIRouter = APIRouter()

    @router.get('/campaigns', response_class=HTMLResponse)
    @router.get('/campaigns/{campaign_id}', response_class=HTMLResponse)
    def page(request: Request, campaign_id: int | None = None) -> HTMLResponse:
        """Открывает список кампаний или редактор потоков."""
        return templates.TemplateResponse(
            request=request, name='editor.html', context={'campaign_id': campaign_id}
        )

    @router.get('/api/campaigns')
    def campaigns(
        request: Request,
        all_campaigns: bool = False,
        q: str = '',
    ) -> list[dict]:
        """Загружает кампании с фильтрами."""
        with request.app.state.editor_services() as service:
            service: EditorService
            try:
                return service.campaigns(all_campaigns, q)
            except IntegrationError as error:
                raise HTTPException(502, str(error)) from None

    @router.get('/api/offers')
    def offers(request: Request, q: str = '') -> list[dict]:
        """Ищет доступные офферы."""
        with request.app.state.editor_services() as service:
            service: EditorService
            try:
                return service.offers(q)
            except IntegrationError as error:
                raise HTTPException(502, str(error)) from None

    @router.get('/api/campaigns/{campaign_id}/streams')
    def streams(
        request: Request,
        campaign_id: int,
        refresh: bool = False,
    ) -> list[dict]:
        """Читает черновики или синхронизирует чистые потоки."""
        with request.app.state.editor_services() as service:
            service: EditorService
            try:
                return [asdict(item) for item in service.load(campaign_id, refresh)]
            except RequestConflict as error:
                raise HTTPException(409, str(error)) from None
            except IntegrationError as error:
                raise HTTPException(502, str(error)) from None

    @router.post('/api/campaigns/{campaign_id}/streams/{stream_id}')
    def change(
        request: Request,
        campaign_id: int,
        stream_id: int,
        data: EditInput,
    ) -> dict:
        """Сохраняет, публикует, отменяет или сверяет черновик потока."""
        with request.app.state.editor_services() as service:
            service: EditorService
            try:
                if data.action in {'push', 'cancel', 'recover', 'reset'}:
                    return asdict(
                        getattr(service, data.action)(
                            campaign_id,
                            stream_id,
                            data.revision,
                        )
                    )
                return asdict(
                    service.change(
                        campaign_id,
                        stream_id,
                        data.revision,
                        data.action,
                        data.offer_id,
                        data.share,
                    )
                )
            except RequestConflict as error:
                raise HTTPException(409, str(error)) from None
            except IntegrationError as error:
                raise HTTPException(502, str(error)) from None
            except ValueError as error:
                raise HTTPException(422, str(error)) from None

    return router
