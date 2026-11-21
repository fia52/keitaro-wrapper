from dataclasses import dataclass
from datetime import datetime

import pycountry


def normalize_name(value: str) -> str:
    """Обеспечивает обязательный префикс и допустимую длину названия."""
    name: str = value.strip()
    if not name:
        raise ValueError('Введите название кампании.')
    if not name.startswith('test_frolov_'):
        name = f'test_frolov_{name}'
    if len(name) > 128 or len(name) == len('test_frolov_'):
        raise ValueError('Название после префикса должно содержать 1–116 символов.')
    return name


def normalize_country(value: str) -> str:
    """Проверяет двухбуквенный код страны по локальному справочнику ISO."""
    code: str = value.strip().upper()
    if len(code) != 2 or pycountry.countries.get(alpha_2=code) is None:
        raise ValueError('Нужен код страны ISO 3166-1, например RU или DE.')
    return code


@dataclass(frozen=True)
class CampaignCommand:
    """Содержит проверенные параметры новой кампании."""

    request_id: str
    name: str
    country_code: str
    offer_id: int

    def __post_init__(self) -> None:
        """Применяет бизнес-правила независимо от способа вызова сценария."""
        object.__setattr__(self, 'name', normalize_name(self.name))
        object.__setattr__(self, 'country_code', normalize_country(self.country_code))
        if self.offer_id <= 0:
            raise ValueError('ID оффера должен быть положительным.')


@dataclass
class Operation:
    """Хранит результат создания и ресурсы для восстановления после сбоя."""

    id: str
    name: str
    country_code: str
    offer_id: int
    created_at: datetime
    status: str = 'pending'
    campaign_id: int | None = None
    google_stream_id: int | None = None
    offer_stream_id: int | None = None
    url: str | None = None
    error: str | None = None


class IntegrationError(Exception):
    """Сообщает о сбое Keitaro без раскрытия секретов и тела ответа."""


class ConfigurationError(Exception):
    """Сообщает об отсутствии обязательных настроек в Keitaro."""


class RequestConflict(Exception):
    """Запрещает повторное использование ключа с другими параметрами."""
