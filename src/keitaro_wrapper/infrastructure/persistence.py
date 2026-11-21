from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from keitaro_wrapper.application.ports import OperationRepository
from keitaro_wrapper.domain.models import Operation


class Base(DeclarativeBase):
    """Предоставляет метаданные SQLAlchemy для миграций."""


class OperationRow(Base):
    """Хранит журнал создания кампаний и идентификаторы ресурсов."""

    __tablename__ = 'operations'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    country_code: Mapped[str] = mapped_column(String(2))
    offer_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_stream_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_stream_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class SqlOperationRepository(OperationRepository):
    """Фиксирует каждый этап отдельной короткой транзакцией."""

    def __init__(self, session: Session) -> None:
        """Принимает сессию текущего запроса."""
        self.session: Session = session

    @staticmethod
    def _values(operation: Operation) -> dict[str, object]:
        """Преобразует доменный объект в поля строки БД."""
        return {
            field: getattr(operation, field) for field in Operation.__dataclass_fields__
        }

    @staticmethod
    def _domain(row: OperationRow) -> Operation:
        """Возвращает доменный объект, не раскрывая ORM наружу."""
        operation: Operation = Operation(
            **{field: getattr(row, field) for field in Operation.__dataclass_fields__}
        )
        # SQLite теряет timezone у DateTime; журнал всегда записывается в UTC.
        if operation.created_at.tzinfo is None:
            operation.created_at = operation.created_at.replace(tzinfo=UTC)
        return operation

    def get(self, request_id: str) -> Operation | None:
        """Читает операцию по ключу повторной отправки."""
        row: OperationRow | None = self.session.get(OperationRow, request_id)
        return self._domain(row) if row is not None else None

    def reserve(self, operation: Operation) -> bool:
        """Использует уникальность первичного ключа для защиты от гонки."""
        self.session.add(OperationRow(**self._values(operation)))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            if self.get(operation.id) is None:
                raise
            return False
        return True

    def save(self, operation: Operation) -> None:
        """Записывает последний известный результат внешней операции."""
        self.session.merge(OperationRow(**self._values(operation)))
        self.session.commit()

    def recent(self) -> list[Operation]:
        """Возвращает не более 30 последних операций."""
        return [
            self._domain(row)
            for row in self.session.scalars(
                select(OperationRow).order_by(OperationRow.created_at.desc()).limit(30)
            )
        ]
