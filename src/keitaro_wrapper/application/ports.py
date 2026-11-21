from abc import ABC, abstractmethod

from keitaro_wrapper.domain.models import CampaignCommand, Operation


class Tracker(ABC):
    """Определяет операции с внешним рекламным трекером."""

    @abstractmethod
    def catalog(self) -> dict[str, object]:
        """Возвращает доступные офферы и выбранные настройки кампании."""

    @abstractmethod
    def create_campaign(self, command: CampaignCommand) -> tuple[int, str]:
        """Создаёт отключённую кампанию и возвращает ID и ссылку."""

    @abstractmethod
    def create_google_stream(self, campaign_id: int, country_code: str) -> int:
        """Создаёт первый поток для указанной страны."""

    @abstractmethod
    def create_offer_stream(self, campaign_id: int, offer_id: int) -> int:
        """Создаёт завершающий поток с оффером."""

    @abstractmethod
    def activate(self, campaign_id: int) -> None:
        """Включает полностью настроенную кампанию."""


class OperationRepository(ABC):
    """Определяет хранение операций независимо от конкретной БД."""

    @abstractmethod
    def get(self, request_id: str) -> Operation | None:
        """Находит ранее начатую операцию."""

    @abstractmethod
    def reserve(self, operation: Operation) -> bool:
        """Атомарно резервирует ключ; возвращает False при повторе."""

    @abstractmethod
    def save(self, operation: Operation) -> None:
        """Фиксирует состояние после каждого внешнего изменения."""

    @abstractmethod
    def recent(self) -> list[Operation]:
        """Возвращает последние операции."""
