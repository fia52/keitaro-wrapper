"""Проверяет редактор через HTTP, SQLite и эмулятор Admin API."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from keitaro_wrapper.application.editor import EditorService
from keitaro_wrapper.config import Settings
from keitaro_wrapper.domain.editor import Draft, Offer, distribute
from keitaro_wrapper.domain.models import RequestConflict
from keitaro_wrapper.infrastructure.editor import KeitaroEditor, SqlDraftRepository
from keitaro_wrapper.infrastructure.keitaro import KeitaroTracker
from keitaro_wrapper.infrastructure.persistence import Base
from keitaro_wrapper.main import create_app


class FakeEditor:
    """Имитирует поток с офферами, Google и неоднозначные сбои записи."""

    def __init__(self) -> None:
        """Создаёт независимое состояние тестового Keitaro."""
        self.remote: dict[int, dict] = {
            10: {
                'id': 10,
                'campaign_id': 1,
                'name': 'Google',
                'position': 1,
                'schema': 'redirect',
                'filters': [{'name': 'country'}],
                'action_options': {'url': 'https://google.com'},
                'offers': [],
            },
            20: {
                'id': 20,
                'campaign_id': 1,
                'name': 'Offers',
                'position': 2,
                'schema': 'landings',
                'filters': [],
                'landings': [{'landing_id': 4}],
                'offers': [
                    {'offer_id': 1, 'share': 34, 'state': 'active'},
                    {'offer_id': 2, 'share': 33, 'state': 'active'},
                    {'offer_id': 3, 'share': 33, 'state': 'active'},
                ],
            },
        }
        self.writes: list[dict] = []
        self.failure: str = ''

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Обслуживает чтение, запись и сбой после фактической записи."""
        path: str = request.url.path.removeprefix('/admin_api/v1/')
        if request.method == 'GET':
            if path == 'campaigns':
                return httpx.Response(
                    200,
                    json=[
                        {'id': 1, 'name': 'test_frolov_demo', 'state': 'active'},
                        {'id': 2, 'name': 'other', 'state': 'active'},
                    ],
                )
            if path == 'offers':
                return httpx.Response(
                    200,
                    json=[
                        {
                            'id': number,
                            'name': f'Offer {number}',
                            'state': 'active' if number < 6 else 'archived',
                        }
                        for number in range(1, 7)
                    ],
                )
            if path == 'campaigns/1/streams':
                return httpx.Response(200, json=list(self.remote.values()))
            if path.startswith('streams/'):
                return httpx.Response(200, json=self.remote[int(path.split('/')[1])])
            return httpx.Response(404)
        payload: dict = json.loads(request.content)
        self.writes.append(payload)
        if self.failure == 'before':
            raise httpx.ReadTimeout('unknown', request=request)
        stream_id: int = int(path.split('/')[1])
        self.remote[stream_id].update(payload)
        if self.failure == 'after':
            raise httpx.ReadTimeout('unknown', request=request)
        if self.failure == '500':
            return httpx.Response(500, text='secret')
        return httpx.Response(200, json=self.remote[stream_id])


@contextmanager
def editor_client(tmp_path: Path) -> Iterator[tuple[TestClient, FakeEditor, Engine]]:
    """Собирает HTTP-приложение с настоящим адаптером и временной БД."""
    settings: Settings = Settings(
        _env_file=None,
        database_url=f'sqlite:///{tmp_path}/editor.db',
        keitaro_api_key=SecretStr('fake'),
    )
    engine: Engine = create_engine(
        settings.database_url,
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(engine)
    fake: FakeEditor = FakeEditor()
    with httpx.Client(
        base_url='https://tracker/admin_api/v1/',
        transport=httpx.MockTransport(fake.handle),
    ) as http:
        http: httpx.Client
        tracker: KeitaroEditor = KeitaroEditor(KeitaroTracker(http, 'domain', 'group'))

        @contextmanager
        def services() -> Iterator[EditorService]:
            """Выдаёт независимую сессию на каждый HTTP-запрос."""
            with Session(engine) as session:
                session: Session
                yield EditorService(tracker, SqlDraftRepository(session))

        app = create_app(settings)
        app.state.editor_services = services
        with TestClient(app) as client:
            client: TestClient
            yield client, fake, engine
    engine.dispose()


def load(client: TestClient, refresh: bool = False) -> dict:
    """Загружает офферный поток, проверяя успешный ответ."""
    response: httpx.Response = client.get(
        '/api/campaigns/1/streams',
        params={'refresh': refresh},
    )
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item['id'] == 20)


def change(
    client: TestClient,
    draft: dict,
    action: str,
    offer_id: int | None = None,
    share: int | None = None,
    status: int = 200,
) -> dict:
    """Отправляет команду с актуальной ревизией."""
    response: httpx.Response = client.post(
        '/api/campaigns/1/streams/20',
        json={
            'revision': draft['revision'],
            'action': action,
            'offer_id': offer_id,
            'share': share,
        },
    )
    assert response.status_code == status, response.text
    return response.json()


def shares(draft: dict) -> list[int]:
    """Читает доли, включая серые архивные строки."""
    return [item['share'] for item in draft['offers']]


def test_lifecycle_and_archive(tmp_path: Path) -> None:
    """Сохраняет черновик, публикует, возвращает архив и сохраняет настройки."""
    with editor_client(tmp_path) as (client, fake, _):
        original: dict = deepcopy(fake.remote[20])
        draft: dict = load(client)
        draft = change(client, draft, 'add', 4)
        assert shares(draft) == [25, 25, 25, 25]
        assert draft['dirty'] and not fake.writes
        assert load(client) == draft
        assert client.get('/api/campaigns/1/streams?refresh=true').status_code == 409
        draft = change(client, draft, 'push')
        assert not draft['dirty']
        assert list(fake.writes[-1]) == ['offers']
        assert fake.remote[20]['landings'] == original['landings']
        draft = change(client, draft, 'remove', 2)
        assert shares(draft) == [34, 0, 33, 33]
        draft = change(client, draft, 'push')
        assert all(item['offer_id'] != 2 for item in fake.remote[20]['offers'])
        draft = load(client, True)
        assert next(item for item in draft['offers'] if item['id'] == 2)['removed']
        draft = change(client, draft, 'restore', 2)
        assert shares(draft) == [25, 25, 25, 25]
        draft = change(client, draft, 'cancel')
        assert not draft['dirty']
        assert next(item for item in draft['offers'] if item['id'] == 2)['removed']


def test_pins_manual_values_and_rounding(tmp_path: Path) -> None:
    """Проверяет ручной ввод, фиксацию и точную сумму после удаления/возврата."""
    with editor_client(tmp_path) as (client, fake, _):
        draft: dict = change(client, load(client), 'add', 4)
        draft = change(client, draft, 'pin', 1)
        draft = change(client, draft, 'remove', 2)
        assert shares(draft) == [25, 0, 38, 37]
        draft = change(client, draft, 'share', 1, 40)
        assert shares(draft) == [40, 0, 30, 30]
        draft = change(client, draft, 'restore', 2)
        assert shares(draft) == [40, 20, 20, 20]
        assert not fake.writes
        draft = change(client, draft, 'pin', 2)
        change(client, draft, 'share', 1, 90, 422)
        assert load(client) == draft


def test_external_delete_and_conflict(tmp_path: Path) -> None:
    """Архивирует внешнее удаление и не затирает внешние настройки."""
    with editor_client(tmp_path) as (client, fake, _):
        load(client)
        fake.remote[20]['offers'] = [
            {'offer_id': 1, 'share': 50, 'state': 'active'},
            {'offer_id': 3, 'share': 50, 'state': 'active'},
        ]
        draft: dict = load(client, True)
        assert draft['offers'][-1]['id'] == 2
        assert draft['offers'][-1]['removed']
        draft = change(client, draft, 'restore', 2)
        fake.remote[20]['filters'] = [{'name': 'external'}]
        change(client, draft, 'push', status=409)
        assert not fake.writes
        draft = change(client, load(client), 'cancel')
        draft = load(client, True)
        assert draft['baseline']['filters'] == [{'name': 'external'}]


@pytest.mark.parametrize('failure', ['before', 'after', '500'])
def test_uncertain_push_and_recovery(tmp_path: Path, failure: str) -> None:
    """Сверяет неопределённый результат без автоматического повторения PUT."""
    with editor_client(tmp_path) as (client, fake, _):
        draft: dict = change(client, load(client), 'add', 4)
        fake.failure = failure
        change(client, draft, 'push', status=502)
        draft = load(client)
        assert draft['uncertain']
        change(client, draft, 'push', status=409)
        assert len(fake.writes) == 1
        fake.failure = ''
        draft = change(client, draft, 'recover')
        assert not draft['uncertain']
        assert draft['dirty'] == (failure == 'before')
        if draft['dirty']:
            draft = change(client, draft, 'push')
        assert not draft['dirty']


def test_last_offer_and_readonly_stream(tmp_path: Path) -> None:
    """Разрешает пустой черновик, блокирует Push и изменение Google."""
    with editor_client(tmp_path) as (client, fake, _):
        draft: dict = load(client)
        for offer_id in [1, 2, 3]:
            draft = change(client, draft, 'remove', offer_id)
        assert shares(draft) == [0, 0, 0]
        change(client, draft, 'push', status=422)
        draft = change(client, draft, 'restore', 1)
        assert shares(draft) == [100, 0, 0]
        google: dict = next(
            item
            for item in client.get(
                '/api/campaigns/1/streams',
            ).json()
            if item['id'] == 10
        )
        assert (
            client.post(
                '/api/campaigns/1/streams/10',
                json={
                    'revision': google['revision'],
                    'action': 'add',
                    'offer_id': 1,
                },
            ).status_code
            == 422
        )
        assert not fake.writes


def test_filters_stale_versions_and_validation(tmp_path: Path) -> None:
    """Проверяет фильтры, чужую кампанию, дубли и валидацию долей."""
    with editor_client(tmp_path) as (client, _, _):
        assert len(client.get('/api/campaigns').json()) == 1
        assert len(client.get('/api/campaigns?all_campaigns=true').json()) == 2
        assert client.get('/api/campaigns?q=nothing').json() == []
        assert client.get('/api/offers?q=6').json() == []
        draft: dict = load(client)
        change(client, draft, 'add', 6, status=422)
        change(client, draft, 'add', 1, status=422)
        change(client, draft, 'share', 1, 101, 422)
        change(client, draft, 'share', 1, 20.5, 422)
        assert (
            client.post(
                '/api/campaigns/2/streams/20',
                json={
                    'revision': draft['revision'],
                    'action': 'remove',
                    'offer_id': 1,
                },
            ).status_code
            == 422
        )
        change(client, draft, 'add', 4)
        change(client, draft, 'remove', 1, status=409)
        assert client.get('/campaigns').status_code == 200
        assert client.get('/campaigns/1').status_code == 200


def test_repository_compare_and_swap(tmp_path: Path) -> None:
    """Не позволяет двум сессиям записать одну ревизию."""
    with editor_client(tmp_path) as (client, _, engine):
        load(client)
        with Session(engine) as left, Session(engine) as right:
            left: Session
            right: Session
            first: Draft = SqlDraftRepository(left).get(20)
            second: Draft = SqlDraftRepository(right).get(20)
            SqlDraftRepository(left).save(first)
            with pytest.raises(RequestConflict):
                SqlDraftRepository(right).save(second)


def test_fixed_total_and_rounding_invariants() -> None:
    """Проверяет сумму и неизменность фиксаций при разном числе офферов."""
    for count in range(1, 15):
        items: list[Offer] = [Offer(1, 'fixed', 25, True)]
        items.extend(Offer(index + 2, 'free', 0) for index in range(count))
        distribute(items)
        assert items[0].share == 25
        assert sum(item.share for item in items) == 100
        assert (
            max(item.share for item in items[1:])
            - min(item.share for item in items[1:])
            <= 1
        )
    with pytest.raises(ValueError):
        distribute([Offer(1, 'fixed', 100, True), Offer(2, 'fixed', 20, True)])


def test_pin_persistence_and_impossible_all_fixed(tmp_path: Path) -> None:
    """Сохраняет фиксацию после Push и не меняет недопустимый черновик."""
    with editor_client(tmp_path) as (client, _, _):
        draft: dict = change(client, load(client), 'add', 4)
        draft = change(client, draft, 'pin', 1)
        draft = change(client, draft, 'push')
        draft = load(client, True)
        assert next(item for item in draft['offers'] if item['id'] == 1)['pinned']
        assert not draft['dirty']
        for offer_id in [2, 3, 4]:
            draft = change(client, draft, 'pin', offer_id)
        change(client, draft, 'remove', 1, status=422)
        assert load(client) == draft
        assert len(draft['offers']) == 4


def test_explicit_reset_and_active_push_lease(tmp_path: Path) -> None:
    """Разрешает явный сброс конфликта, не снимая активную блокировку Push."""
    from time import time

    with editor_client(tmp_path) as (client, fake, engine):
        draft: dict = change(client, load(client), 'add', 4)
        fake.failure = 'after'
        change(client, draft, 'push', status=502)
        draft = load(client)
        fake.remote[20]['offers'] = [
            {'offer_id': 5, 'share': 100, 'state': 'active'},
        ]
        change(client, draft, 'recover', status=409)
        draft = change(client, draft, 'reset')
        assert not draft['dirty'] and not draft['uncertain']
        assert sum(not item['removed'] for item in draft['offers']) == 1
        assert any(item['id'] == 4 and item['removed'] for item in draft['offers'])
        with Session(engine) as session:
            session: Session
            repository: SqlDraftRepository = SqlDraftRepository(session)
            domain: Draft = repository.get(20)
            domain.publishing = True
            domain.publishing_until = time() + 120
            repository.save(domain)
        draft = load(client)
        change(client, draft, 'recover', status=409)
        change(client, draft, 'reset', status=409)
        with Session(engine) as session:
            session: Session
            repository = SqlDraftRepository(session)
            domain = repository.get(20)
            domain.publishing_until = 0
            repository.save(domain)
        draft = change(client, load(client), 'recover')
        assert not draft['publishing']
