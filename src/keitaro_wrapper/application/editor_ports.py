"""Определяет порты редактора независимо от HTTP и ORM."""

from abc import ABC, abstractmethod

from keitaro_wrapper.domain.editor import Draft


class EditorTracker(ABC):
    """Предоставляет чтение кампаний и изменение состава офферов."""

    @abstractmethod
    def campaigns(self) -> list[dict]:
        """Возвращает кампании трекера."""

    @abstractmethod
    def streams(self, campaign_id: int) -> list[dict]:
        """Возвращает полные потоки выбранной кампании."""

    @abstractmethod
    def stream(self, stream_id: int) -> dict:
        """Читает поток непосредственно перед публикацией."""

    @abstractmethod
    def offers(self) -> list[dict]:
        """Возвращает каталог офферов."""

    @abstractmethod
    def update_offers(self, stream_id: int, offers: list[dict]) -> None:
        """Заменяет только состав офферов потока."""


class DraftRepository(ABC):
    """Хранит черновики с оптимистической блокировкой."""

    @abstractmethod
    def get(self, stream_id: int) -> Draft | None:
        """Находит черновик потока."""

    @abstractmethod
    def list(self, campaign_id: int) -> list[Draft]:
        """Возвращает сохранённые потоки кампании."""

    @abstractmethod
    def save(self, draft: Draft) -> None:
        """Атомарно сохраняет, отклоняя устаревшую ревизию."""
