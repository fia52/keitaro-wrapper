from uuid import uuid4

import httpx

from keitaro_wrapper.application.ports import Tracker
from keitaro_wrapper.domain.models import (
    CampaignCommand,
    ConfigurationError,
    IntegrationError,
)


class KeitaroTracker(Tracker):
    """Адаптирует Admin API Keitaro к сценариям приложения."""

    def __init__(self, client: httpx.Client, domain: str, group: str) -> None:
        """Принимает настроенный HTTP-клиент и имена справочников."""
        self.client: httpx.Client = client
        self.domain: str = domain
        self.group: str = group

    def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> object:
        """Выполняет один запрос без повторов потенциально опасных POST."""
        response: httpx.Response
        try:
            response = self.client.request(method, path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise IntegrationError(
                f'Keitaro: {method} {path}, HTTP {error.response.status_code}.'
            ) from None
        except (httpx.RequestError, ValueError) as error:
            reason: str
            if isinstance(error, httpx.ConnectTimeout):
                reason = 'Истекло время подключения к Keitaro'
            elif isinstance(error, httpx.TimeoutException):
                reason = 'Истекло время ожидания ответа Keitaro'
            elif isinstance(error, httpx.ConnectError):
                reason = 'Не удалось подключиться к Keitaro'
            elif isinstance(error, httpx.RequestError):
                reason = 'Сетевой сбой при обращении к Keitaro'
            else:
                reason = 'Keitaro вернул ответ в неверном формате: ожидался JSON'
            advice: str = (
                'Проверьте сеть, VPN/прокси и доступность сервера.'
                if method == 'GET'
                else 'Перед новой попыткой проверьте ресурсы в админке.'
            )
            raise IntegrationError(f'{reason}: {method} {path}. {advice}') from None

    def _object(self, method: str, path: str, payload: dict[str, object]) -> dict:
        """Проверяет структуру ответа после изменения ресурса."""
        result: object = self._request(method, path, payload)
        if not isinstance(result, dict) or not isinstance(result.get('id'), int):
            raise IntegrationError('Keitaro вернул ресурс без числового ID.')
        return result

    def _list(self, path: str) -> list[dict]:
        """Проверяет структуру справочника Keitaro."""
        result: object = self._request('GET', path)
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise IntegrationError('Keitaro вернул неверный формат справочника.')
        return result

    def catalog(self) -> dict[str, object]:
        """Находит настроенные домен и группу, загружает активные офферы."""
        domains: list[dict] = self._list('domains')
        groups: list[dict] = self._list('groups?type=campaigns')
        offers: list[dict] = self._list('offers')
        domain: dict | None = next(
            (
                item
                for item in domains
                if item.get('name') == self.domain and item.get('state') == 'active'
            ),
            None,
        )
        group: dict | None = next(
            (item for item in groups if item.get('name') == self.group), None
        )
        if domain is None or group is None:
            raise ConfigurationError('В Keitaro не найдены активный домен или группа.')
        return {
            'domain_id': int(domain['id']),
            'domain': self.domain,
            'group_id': int(group['id']),
            'group': self.group,
            'offers': [
                {'id': item['id'], 'name': item['name']}
                for item in offers
                if item.get('state') == 'active'
            ],
        }

    def create_campaign(self, command: CampaignCommand) -> tuple[int, str]:
        """Проверяет оффер и создаёт отключённую кампанию без источника."""
        catalog: dict[str, object] = self.catalog()
        offers: object = catalog['offers']
        if not isinstance(offers, list) or not any(
            item['id'] == command.offer_id for item in offers
        ):
            raise ConfigurationError('Указанный активный оффер не найден.')
        alias: str = f'test_frolov_{uuid4().hex[:16]}'
        result: dict = self._object(
            'POST',
            'campaigns',
            {
                'name': command.name,
                'alias': alias,
                'state': 'disabled',
                'type': 'position',
                'domain_id': catalog['domain_id'],
                'group_id': str(catalog['group_id']),
            },
        )
        return result['id'], f'https://{self.domain}/{alias}'

    def create_google_stream(self, campaign_id: int, country_code: str) -> int:
        """Создаёт регулярный поток с фильтром страны и HTTP 302 в Google."""
        result: dict = self._object(
            'POST',
            'streams',
            {
                'campaign_id': campaign_id,
                'name': 'Country → Google',
                'type': 'regular',
                'position': 1,
                'state': 'active',
                'schema': 'redirect',
                'action_type': 'http',
                'action_options': {'url': 'https://www.google.com/'},
                'collect_clicks': True,
                'filter_or': False,
                'filters': [
                    {'name': 'country', 'mode': 'accept', 'payload': [country_code]}
                ],
            },
        )
        return result['id']

    def create_offer_stream(self, campaign_id: int, offer_id: int) -> int:
        """Создаёт поток по умолчанию со стопроцентной долей оффера."""
        result: dict = self._object(
            'POST',
            'streams',
            {
                'campaign_id': campaign_id,
                'name': 'Other countries → Offer',
                'type': 'default',
                'position': 2,
                'state': 'active',
                'schema': 'landings',
                'action_type': 'http',
                'collect_clicks': True,
                'filters': [],
                'landings': [],
                'offers': [{'offer_id': offer_id, 'share': 100, 'state': 'active'}],
            },
        )
        return result['id']

    def activate(self, campaign_id: int) -> None:
        """Включает кампанию после успешного создания двух потоков."""
        self._object('PUT', f'campaigns/{campaign_id}', {'state': 'active'})
