from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from keitaro_wrapper.application.editor import EditorService
from keitaro_wrapper.application.services import CampaignService
from keitaro_wrapper.config import Settings
from keitaro_wrapper.infrastructure.editor import KeitaroEditor, SqlDraftRepository
from keitaro_wrapper.infrastructure.keitaro import KeitaroTracker
from keitaro_wrapper.infrastructure.persistence import SqlOperationRepository
from keitaro_wrapper.presentation.api import make_router
from keitaro_wrapper.presentation.editor import make_editor_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собирает приложение, адаптеры и жизненный цикл ресурсов."""
    config: Settings = settings or Settings()
    engine: Engine = create_engine(
        config.database_url,
        connect_args={'check_same_thread': False}
        if config.database_url.startswith('sqlite')
        else {},
    )
    client: httpx.Client = httpx.Client(
        base_url=f'{config.keitaro_base_url.rstrip("/")}/admin_api/v1/',
        headers={
            'Api-Key': config.keitaro_api_key.get_secret_value(),
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        },
        timeout=httpx.Timeout(20, connect=5),
    )
    tracker: KeitaroTracker = KeitaroTracker(
        client, config.keitaro_domain, config.keitaro_group
    )

    @contextmanager
    def services() -> Iterator[CampaignService]:
        """Выделяет сессию БД на один сценарий и закрывает её после запроса."""
        with Session(engine) as session:
            session: Session
            yield CampaignService(tracker, SqlOperationRepository(session))

    @contextmanager
    def editor_services() -> Iterator[EditorService]:
        """Создаёт редактор с отдельной сессией БД на запрос."""
        with Session(engine) as session:
            session: Session
            yield EditorService(KeitaroEditor(tracker), SqlDraftRepository(session))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Освобождает пул соединений и HTTP-клиент при остановке."""
        try:
            yield
        finally:
            client.close()
            engine.dispose()

    app: FastAPI = FastAPI(title='Keitaro Wrapper', lifespan=lifespan)
    app.state.services = services
    app.state.editor_services = editor_services
    web_path: Path = Path(__file__).parent / 'presentation'
    app.include_router(make_router(Jinja2Templates(directory=web_path / 'templates')))
    app.include_router(
        make_editor_router(Jinja2Templates(directory=web_path / 'templates'))
    )
    app.mount('/static', StaticFiles(directory=web_path / 'static'), name='static')
    return app
