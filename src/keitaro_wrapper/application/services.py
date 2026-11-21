from datetime import UTC, datetime

from keitaro_wrapper.application.ports import OperationRepository, Tracker
from keitaro_wrapper.domain.models import (
    CampaignCommand,
    ConfigurationError,
    IntegrationError,
    Operation,
    RequestConflict,
)


class CampaignService:
    """Координирует создание кампании и фиксацию частичных результатов."""

    def __init__(self, tracker: Tracker, repository: OperationRepository) -> None:
        """Принимает порты трекера и хранилища."""
        self.tracker: Tracker = tracker
        self.repository: OperationRepository = repository

    def create(self, command: CampaignCommand) -> Operation:
        """Создаёт кампанию один раз для ключа, сохраняя ID каждого ресурса."""
        operation: Operation = Operation(
            id=command.request_id,
            name=command.name,
            country_code=command.country_code,
            offer_id=command.offer_id,
            created_at=datetime.now(UTC),
        )
        if not self.repository.reserve(operation):
            existing: Operation | None = self.repository.get(command.request_id)
            if existing is None:
                raise RequestConflict('Не удалось получить зарезервированную операцию.')
            if (existing.name, existing.country_code, existing.offer_id) != (
                command.name,
                command.country_code,
                command.offer_id,
            ):
                raise RequestConflict(
                    'Этот request_id уже использован с другими данными.'
                )
            return existing
        try:
            operation.campaign_id, operation.url = self.tracker.create_campaign(command)
            self.repository.save(operation)
            operation.google_stream_id = self.tracker.create_google_stream(
                operation.campaign_id, command.country_code
            )
            self.repository.save(operation)
            operation.offer_stream_id = self.tracker.create_offer_stream(
                operation.campaign_id, command.offer_id
            )
            self.repository.save(operation)
            self.tracker.activate(operation.campaign_id)
            operation.status = 'completed'
        except (IntegrationError, ConfigurationError) as error:
            operation.status = 'failed'
            operation.error = str(error)
        self.repository.save(operation)
        return operation

    def catalog(self) -> dict[str, object]:
        """Предоставляет данные формы через порт трекера."""
        return self.tracker.catalog()

    def recent(self) -> list[Operation]:
        """Предоставляет историю создания кампаний."""
        return self.repository.recent()
