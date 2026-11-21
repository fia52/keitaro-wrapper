"""Реализует HTTP и SQLAlchemy адаптеры редактора."""

from dataclasses import asdict

from sqlalchemy import JSON, Integer, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from keitaro_wrapper.application.editor_ports import DraftRepository, EditorTracker
from keitaro_wrapper.domain.editor import Draft, Offer
from keitaro_wrapper.domain.models import IntegrationError, RequestConflict
from keitaro_wrapper.infrastructure.keitaro import KeitaroTracker
from keitaro_wrapper.infrastructure.persistence import Base


class EditorRow(Base):
    """Хранит снимок и черновик, включая удалённые офферы."""

    __tablename__ = 'stream_drafts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)


class SqlDraftRepository(DraftRepository):
    """Защищает черновики от перезаписи из другой вкладки."""

    def __init__(self, session: Session) -> None:
        """Принимает сессию текущего сценария."""
        self.session: Session = session

    @staticmethod
    def decode(row: EditorRow) -> Draft:
        """Преобразует JSON в доменные значения."""
        data: dict = dict(row.data)
        data['offers'] = [Offer(**item) for item in data['offers']]
        data['revision'] = row.revision
        return Draft(**data)

    def get(self, stream_id: int) -> Draft | None:
        """Читает актуальную версию потока."""
        row: EditorRow | None = self.session.get(EditorRow, stream_id)
        return self.decode(row) if row else None

    def list(self, campaign_id: int) -> list[Draft]:
        """Загружает сохранённые потоки в порядке Keitaro."""
        rows: list[EditorRow] = list(
            self.session.scalars(
                select(EditorRow).where(EditorRow.campaign_id == campaign_id)
            )
        )
        return sorted(
            [self.decode(row) for row in rows],
            key=lambda draft: (draft.baseline.get('position', 0), draft.id),
        )

    def save(self, draft: Draft) -> None:
        """Применяет compare-and-swap и увеличивает ревизию."""
        data: dict = asdict(draft)
        next_revision: int = draft.revision + 1
        if draft.revision == 0:
            self.session.add(
                EditorRow(
                    id=draft.id,
                    campaign_id=draft.campaign_id,
                    revision=next_revision,
                    data=data,
                )
            )
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                raise RequestConflict(
                    'Поток уже загружен. Обновите страницу.'
                ) from None
        else:
            result = self.session.execute(
                update(EditorRow)
                .where(
                    EditorRow.id == draft.id,
                    EditorRow.revision == draft.revision,
                )
                .values(data=data, revision=next_revision),
                execution_options={'synchronize_session': False},
            )
            if result.rowcount != 1:
                self.session.rollback()
                raise RequestConflict('Черновик изменился. Обновите страницу.')
            self.session.commit()
            self.session.expire_all()
        draft.revision = next_revision


class KeitaroEditor(EditorTracker):
    """Скрывает протокол Keitaro за портом редактора."""

    def __init__(self, tracker: KeitaroTracker) -> None:
        """Использует общий настроенный клиент трекера."""
        self.tracker: KeitaroTracker = tracker

    def campaigns(self) -> list[dict]:
        """Читает доступные кампании."""
        return self.tracker._list('campaigns')

    def streams(self, campaign_id: int) -> list[dict]:
        """Читает состав кампании и полные параметры каждого потока."""
        items: list[dict] = self.tracker._list(f'campaigns/{campaign_id}/streams')
        return [self.stream(int(item['id'])) for item in items]

    def stream(self, stream_id: int) -> dict:
        """Проверяет ответ чтения отдельного потока."""
        result: object = self.tracker._request('GET', f'streams/{stream_id}')
        if not isinstance(result, dict) or result.get('id') != stream_id:
            raise IntegrationError('Keitaro вернул неверный поток.')
        return result

    def offers(self) -> list[dict]:
        """Читает каталог независимо от настроек создания кампаний."""
        return self.tracker._list('offers')

    def update_offers(self, stream_id: int, offers: list[dict]) -> None:
        """Меняет офферы, сохраняя фильтры, лендинги и прочие настройки."""
        self.tracker._object('PUT', f'streams/{stream_id}', {'offers': offers})
