from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from keitaro_wrapper.application.services import CampaignService
from keitaro_wrapper.config import Settings
from keitaro_wrapper.domain.models import Operation
from keitaro_wrapper.infrastructure.keitaro import KeitaroTracker
from keitaro_wrapper.infrastructure.persistence import Base, SqlOperationRepository
from keitaro_wrapper.main import create_app


class FakeKeitaro:
    """Эмулирует API и фиксирует внешние изменения без доступа к сети."""

    def __init__(self, fail_at: int = 0, timeout_at: int = 0) -> None:
        """Задаёт номер изменяющего запроса для имитации сбоя."""
        self.writes: list[tuple[str, str, dict]] = []
        self.fail_at: int = fail_at
        self.timeout_at: int = timeout_at

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Возвращает справочники либо ответ на создание ресурса."""
        import json

        path: str = request.url.path.rsplit('/', 1)[-1]
        if request.method == 'GET':
            catalog: dict[str, list[dict]] = {
                'domains': [{'id': 1, 'name': 'kylovoa.com', 'state': 'active'}],
                'groups': [{'id': 2, 'name': 'FORTESTS'}],
                'offers': [{'id': 3, 'name': 'Test offer', 'state': 'active'}],
            }
            return httpx.Response(200, json=catalog[path])
        payload: dict = json.loads(request.content)
        self.writes.append((request.method, path, payload))
        if len(self.writes) == self.timeout_at:
            raise httpx.ReadTimeout('read timed out', request=request)
        if len(self.writes) == self.fail_at:
            return httpx.Response(500, text='private upstream response')
        return httpx.Response(200, json={'id': 100 + len(self.writes)})


@contextmanager
def test_client(tmp_path: Path, fake: FakeKeitaro) -> Iterator[TestClient]:
    """Собирает приложение с SQLite и HTTP-заглушкой Keitaro."""
    settings: Settings = Settings(
        _env_file=None,
        database_url=f'sqlite:///{tmp_path}/test.db',
        keitaro_api_key=SecretStr('test-secret'),
    )
    engine: Engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with httpx.Client(
        base_url='https://tracker/admin_api/v1/',
        transport=httpx.MockTransport(fake.handle),
    ) as http:
        http: httpx.Client
        tracker: KeitaroTracker = KeitaroTracker(http, 'kylovoa.com', 'FORTESTS')

        @contextmanager
        def services() -> Iterator[CampaignService]:
            """Открывает независимую сессию на запрос тестового приложения."""
            with Session(engine) as session:
                session: Session
                yield CampaignService(tracker, SqlOperationRepository(session))

        app: FastAPI = create_app(settings)
        app.state.services = services
        with TestClient(app) as client:
            client: TestClient
            yield client
    engine.dispose()


# Эта функция — фабрика контекста, а не самостоятельный тест pytest.
test_client.__test__ = False


def command() -> dict[str, object]:
    """Готовит уникальную команду для HTTP-сценария."""
    return {
        'request_id': str(uuid4()),
        'name': 'campaign1',
        'country_code': 'de',
        'offer_id': 3,
    }


def test_creation_and_idempotency(tmp_path: Path) -> None:
    """Проверяет реальные payload, порядок активации и отсутствие дублей."""
    fake: FakeKeitaro = FakeKeitaro()
    data: dict[str, object] = command()
    with test_client(tmp_path, fake) as client:
        client: TestClient
        response: httpx.Response = client.post('/api/campaigns', json=data)
        assert response.status_code == 200
        operation: dict = response.json()
        assert operation['status'] == 'completed'
        assert operation['name'] == 'test_frolov_campaign1'
        assert operation['country_code'] == 'DE'
        assert operation['google_stream_id'] == 102
        assert operation['offer_stream_id'] == 103
        assert client.post('/api/campaigns', json=data).json() == operation
        assert len(fake.writes) == 4
        assert fake.writes[0][2]['state'] == 'disabled'
        assert fake.writes[0][2]['group_id'] == '2'
        assert fake.writes[0][2]['domain_id'] == 1
        assert 'traffic_source_id' not in fake.writes[0][2]
        assert fake.writes[1][2]['filters'] == [
            {'name': 'country', 'mode': 'accept', 'payload': ['DE']}
        ]
        assert fake.writes[1][2]['action_options']['url'] == 'https://www.google.com/'
        assert fake.writes[2][2]['type'] == 'default'
        assert fake.writes[2][2]['offers'] == [
            {'offer_id': 3, 'share': 100, 'state': 'active'}
        ]
        assert fake.writes[-1] == ('PUT', '101', {'state': 'active'})
        assert len(client.get('/api/operations').json()) == 1
        assert client.get('/').status_code == 200
        assert client.get('/static/app.js').status_code == 200


@pytest.mark.parametrize('failure', [1, 2, 3, 4])
def test_partial_failure(tmp_path: Path, failure: int) -> None:
    """Сохраняет известные ID, скрывает ответ upstream и не повторяет изменения."""
    fake: FakeKeitaro = FakeKeitaro(fail_at=failure)
    data: dict[str, object] = command()
    with test_client(tmp_path, fake) as client:
        client: TestClient
        operation: dict = client.post('/api/campaigns', json=data).json()
        assert operation['status'] == 'failed'
        assert operation['campaign_id'] == (101 if failure > 1 else None)
        assert operation['google_stream_id'] == (102 if failure > 2 else None)
        assert operation['offer_stream_id'] == (103 if failure > 3 else None)
        assert 'private' not in operation['error']
        assert client.post('/api/campaigns', json=data).json() == operation
        assert len(fake.writes) == failure


@pytest.mark.parametrize(
    'field,value',
    [
        ('country_code', 'XX'),
        ('country_code', 'DEU'),
        ('name', '   '),
        ('offer_id', 0),
        ('request_id', 'invalid'),
        ('name', 'test_frolov_'),
    ],
)
def test_validation(tmp_path: Path, field: str, value: object) -> None:
    """Отклоняет неверный ввод до обращения к Keitaro."""
    fake: FakeKeitaro = FakeKeitaro()
    data: dict[str, object] = command()
    data[field] = value
    with test_client(tmp_path, fake) as client:
        client: TestClient
        assert client.post('/api/campaigns', json=data).status_code == 422
        assert fake.writes == []


def test_conflict_and_missing_offer(tmp_path: Path) -> None:
    """Не переиспользует ключ с другими данными и не создаёт неизвестный оффер."""
    fake: FakeKeitaro = FakeKeitaro()
    data: dict[str, object] = command()
    with test_client(tmp_path, fake) as client:
        client: TestClient
        assert client.post('/api/campaigns', json=data).json()['status'] == 'completed'
        data['offer_id'] = 4
        assert client.post('/api/campaigns', json=data).status_code == 409
        data['request_id'] = str(uuid4())
        operation: dict = client.post('/api/campaigns', json=data).json()
        assert operation['status'] == 'failed'
        assert operation['campaign_id'] is None
        assert len(fake.writes) == 4


def test_uncertain_post_is_not_retried(tmp_path: Path) -> None:
    """Не повторяет создание после тайм-аута с неизвестным результатом."""
    fake: FakeKeitaro = FakeKeitaro(timeout_at=1)
    data: dict[str, object] = command()
    with test_client(tmp_path, fake) as client:
        client: TestClient
        operation: dict = client.post('/api/campaigns', json=data).json()
        assert operation['status'] == 'failed'
        assert operation['campaign_id'] is None
        assert 'Перед новой попыткой' in operation['error']
        client.post('/api/campaigns', json=data)
        assert len(fake.writes) == 1


def test_pending_operation_is_not_executed_twice(tmp_path: Path) -> None:
    """Возвращает pending, если ключ уже занят другим запросом или процессом."""
    fake: FakeKeitaro = FakeKeitaro()
    data: dict[str, object] = command()
    with test_client(tmp_path, fake) as client:
        client: TestClient
        with client.app.state.services() as service:
            service: CampaignService
            assert service.repository.reserve(
                Operation(
                    id=str(data['request_id']),
                    name='test_frolov_campaign1',
                    country_code='DE',
                    offer_id=3,
                    created_at=datetime.now(UTC),
                )
            )
        response: httpx.Response = client.post('/api/campaigns', json=data)
        assert response.json()['status'] == 'pending'
        assert fake.writes == []


def test_missing_group_does_not_create_resources(tmp_path: Path) -> None:
    """Сообщает об отсутствующей группе до любых внешних изменений."""
    fake: FakeKeitaro = FakeKeitaro()
    with test_client(tmp_path, fake) as client:
        client: TestClient
        with client.app.state.services() as service:
            service: CampaignService
            service.tracker.group = 'missing'
        assert client.get('/api/catalog').status_code == 503
        operation: dict = client.post('/api/campaigns', json=command()).json()
        assert operation['status'] == 'failed'
        assert operation['campaign_id'] is None
        assert fake.writes == []


@pytest.mark.parametrize(
    'failure, expected',
    [
        ('connect_timeout', 'время подключения'),
        ('read_timeout', 'время ожидания ответа'),
        ('connect', 'Не удалось подключиться'),
        ('network', 'Сетевой сбой'),
        ('json', 'ожидался JSON'),
    ],
)
def test_catalog_connection_errors(
    tmp_path: Path,
    failure: str,
    expected: str,
) -> None:
    """Объясняет сбой чтения без предупреждения о создании ресурсов и секретов."""
    fake: FakeKeitaro = FakeKeitaro()

    def fail(request: httpx.Request) -> httpx.Response:
        """Возвращает выбранный сетевой сбой или HTML вместо JSON."""
        if failure == 'connect_timeout':
            raise httpx.ConnectTimeout('private upstream error', request=request)
        if failure == 'read_timeout':
            raise httpx.ReadTimeout('private upstream error', request=request)
        if failure == 'connect':
            raise httpx.ConnectError('private upstream error', request=request)
        if failure == 'network':
            raise httpx.ReadError('private upstream error', request=request)
        return httpx.Response(200, text='<html>private upstream error</html>')

    fake.handle = fail
    with test_client(tmp_path, fake) as client:
        client: TestClient
        response: httpx.Response = client.get('/api/catalog')
        assert response.status_code == 502
        detail: str = response.json()['detail']
        assert expected in detail
        assert 'GET domains' in detail
        assert 'Проверьте сеть' in detail
        assert 'ресурсы в админке' not in detail
        assert 'private' not in detail
        assert 'test-secret' not in detail
        assert fake.writes == []
