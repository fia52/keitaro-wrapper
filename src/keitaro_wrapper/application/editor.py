"""Координирует черновики, синхронизацию и публикацию офферов."""

from copy import deepcopy
from time import time

from keitaro_wrapper.application.editor_ports import DraftRepository, EditorTracker
from keitaro_wrapper.domain.editor import Draft, Offer, distribute, signature
from keitaro_wrapper.domain.models import IntegrationError, RequestConflict


class EditorService:
    """Применяет локальные изменения и публикует только по явной команде."""

    def __init__(self, tracker: EditorTracker, repository: DraftRepository) -> None:
        """Принимает порты внешнего трекера и хранилища."""
        self.tracker: EditorTracker = tracker
        self.repository: DraftRepository = repository

    def campaigns(self, all_campaigns: bool, query: str) -> list[dict]:
        """Фильтрует список по префиксу и пользовательскому поиску."""
        return [
            {'id': item['id'], 'name': item['name'], 'state': item.get('state')}
            for item in self.tracker.campaigns()
            if (all_campaigns or item['name'].startswith('test_frolov_'))
            and (query.lower() in item['name'].lower() or query == str(item['id']))
        ]

    def offers(self, query: str) -> list[dict]:
        """Ищет активные офферы по ID или названию без загрузки настроек создания."""
        return [
            {'id': item['id'], 'name': item['name']}
            for item in self.tracker.offers()
            if item.get('state') == 'active'
            and (query.lower() in item['name'].lower() or query == str(item['id']))
        ][:100]

    def load(self, campaign_id: int, refresh: bool = False) -> list[Draft]:
        """Возвращает сохранённые черновики либо безопасно обновляет снимки."""
        existing: list[Draft] = self.repository.list(campaign_id)
        if existing and not refresh:
            return existing
        if any(item.dirty or item.publishing or item.uncertain for item in existing):
            raise RequestConflict(
                'Сначала опубликуйте или отмените черновики. '
                'Неопределённый результат Push проверьте через «Сверить после сбоя».'
            )
        streams: list[dict] = self.tracker.streams(campaign_id)
        names: dict[int, str] = {
            int(item['id']): item['name'] for item in self.tracker.offers()
        }
        remote_ids: set[int] = {int(item['id']) for item in streams}
        if any(item.id not in remote_ids for item in existing):
            raise RequestConflict(
                'В Keitaro удалён целый поток. Его локальные данные сохранены; '
                'редактор не восстанавливает сами потоки.'
            )
        for stream in streams:
            draft: Draft = self.repository.get(int(stream['id'])) or Draft(
                int(stream['id']), campaign_id, {}
            )
            self.reconcile(draft, stream, names)
            self.repository.save(draft)
        return self.repository.list(campaign_id)

    @staticmethod
    def reconcile(draft: Draft, stream: dict, names: dict[int, str]) -> None:
        """Обновляет снимок, сохраняя локальную историю удалённых офферов."""
        previous: dict[int, Offer] = {item.id: item for item in draft.offers}
        current: list[Offer] = []
        for item in stream.get('offers', []):
            offer_id: int = int(item['offer_id'])
            old: Offer | None = previous.pop(offer_id, None)
            removed: bool = item.get('state', 'active') != 'active'
            share: int = 0 if removed else int(item['share'])
            current.append(
                Offer(
                    offer_id,
                    names.get(offer_id, old.name if old else f'Offer #{offer_id}'),
                    share,
                    bool(old and old.pinned and old.share == share),
                    removed,
                )
            )
        for old in previous.values():
            old.removed = True
            old.share = 0
            old.pinned = False
            current.append(old)
        draft.baseline = deepcopy(stream)
        draft.offers = current
        draft.dirty = False
        draft.publishing = False
        draft.uncertain = False

    def require(self, campaign_id: int, stream_id: int, revision: int) -> Draft:
        """Проверяет принадлежность кампании и актуальность версии."""
        draft: Draft | None = self.repository.get(stream_id)
        if draft is None or draft.campaign_id != campaign_id:
            raise ValueError('Поток не найден в выбранной кампании.')
        if draft.revision != revision:
            raise RequestConflict('Данные изменились. Обновите страницу.')
        return draft

    def change(
        self,
        campaign_id: int,
        stream_id: int,
        revision: int,
        action: str,
        offer_id: int | None,
        share: int | None,
    ) -> Draft:
        """Изменяет состав и доли только в локальной базе."""
        draft: Draft = self.require(campaign_id, stream_id, revision)
        if draft.publishing or draft.uncertain:
            raise RequestConflict('Сначала выполните сверку после незавершённого Push.')
        if not draft.editable:
            raise ValueError('Поток без офферов доступен только для просмотра.')
        offer: Offer | None = next(
            (item for item in draft.offers if item.id == offer_id), None
        )
        if action in {'add', 'restore'}:
            matches: list[dict] = self.offers(str(offer_id))
            selected: dict | None = next(
                (item for item in matches if item['id'] == offer_id), None
            )
            if not selected:
                raise ValueError('Активный оффер с таким ID не найден.')
            if offer and not offer.removed:
                raise ValueError('Оффер уже присутствует в потоке.')
            if offer:
                offer.removed = False
                offer.pinned = False
            else:
                draft.offers.append(Offer(int(selected['id']), selected['name'], 0))
            distribute(draft.offers)
        elif offer is None or offer.removed:
            raise ValueError('Активный оффер не найден.')
        elif action == 'remove':
            offer.removed = True
            offer.pinned = False
            distribute(draft.offers)
        elif action == 'pin':
            offer.pinned = not offer.pinned
            # Фиксация сама по себе не меняет опубликованное распределение.
        elif action == 'share':
            if share is None or not 0 <= share <= 100:
                raise ValueError('Доля должна быть целым числом от 0 до 100.')
            offer.share = share
            distribute(draft.offers, offer.id)
        else:
            raise ValueError('Неизвестное действие.')
        draft.dirty = self.payload(draft) != signature(draft.baseline)['offers']
        self.repository.save(draft)
        return draft

    @staticmethod
    def payload(draft: Draft) -> list[dict]:
        """Готовит активный состав в стабильном порядке для сравнения."""
        return sorted(
            [
                {'offer_id': offer.id, 'share': offer.share, 'state': 'active'}
                for offer in draft.offers
                if not offer.removed
            ],
            key=lambda item: item['offer_id'],
        )

    def cancel(self, campaign_id: int, stream_id: int, revision: int) -> Draft:
        """Отменяет черновик к последнему снимку, сохраняя архив."""
        draft: Draft = self.require(campaign_id, stream_id, revision)
        if draft.publishing or draft.uncertain:
            raise RequestConflict('Используйте сверку после сбоя.')
        names: dict[int, str] = {offer.id: offer.name for offer in draft.offers}
        self.reconcile(draft, deepcopy(draft.baseline), names)
        self.repository.save(draft)
        return draft

    def recover(self, campaign_id: int, stream_id: int, revision: int) -> Draft:
        """Сверяет результат сбоя, сохраняя неопубликованный черновик."""
        draft: Draft = self.require(campaign_id, stream_id, revision)
        if draft.publishing and time() < draft.publishing_until:
            raise RequestConflict(
                'Push выполняется. Повторите сверку через две минуты.'
            )
        remote: dict = self.tracker.stream(stream_id)
        if signature(remote) == signature(draft.baseline):
            draft.publishing = False
            draft.uncertain = False
        elif signature(remote)['offers'] == self.payload(draft):
            names: dict[int, str] = {offer.id: offer.name for offer in draft.offers}
            self.reconcile(draft, remote, names)
        else:
            raise RequestConflict(
                'В Keitaro другой состав или настройки. '
                'Проверьте поток в админке перед восстановлением.'
            )
        self.repository.save(draft)
        return draft

    def reset(self, campaign_id: int, stream_id: int, revision: int) -> Draft:
        """Явно отбрасывает черновик в пользу текущего состояния Keitaro."""
        draft: Draft = self.require(campaign_id, stream_id, revision)
        if draft.publishing and time() < draft.publishing_until:
            raise RequestConflict('Дождитесь завершения Push перед сбросом.')
        remote: dict = self.tracker.stream(stream_id)
        names: dict[int, str] = {offer.id: offer.name for offer in draft.offers}
        self.reconcile(draft, remote, names)
        self.repository.save(draft)
        return draft

    def push(self, campaign_id: int, stream_id: int, revision: int) -> Draft:
        """Публикует один поток с проверкой конфликта и чтением результата."""
        draft: Draft = self.require(campaign_id, stream_id, revision)
        if draft.publishing or draft.uncertain:
            raise RequestConflict('Сначала сверьте результат предыдущего Push.')
        payload: list[dict] = self.payload(draft)
        if (
            not draft.editable
            or not payload
            or sum(item['share'] for item in payload) != 100
        ):
            raise ValueError(
                'Для публикации нужны активные офферы с суммой долей 100%.'
            )
        draft.publishing = True
        draft.publishing_until = time() + 120
        self.repository.save(draft)
        writing: bool = False
        try:
            remote: dict = self.tracker.stream(stream_id)
            if signature(remote) != signature(draft.baseline):
                raise RequestConflict(
                    'Поток изменён в Keitaro. Отмените черновик и обновите потоки.'
                )
            writing = True
            self.tracker.update_offers(stream_id, payload)
            verified: dict = self.tracker.stream(stream_id)
            expected: dict = deepcopy(remote)
            expected['offers'] = payload
            if signature(verified) != signature(expected):
                raise IntegrationError('Результат Push отличается. Выполните сверку.')
            names: dict[int, str] = {offer.id: offer.name for offer in draft.offers}
            self.reconcile(draft, verified, names)
        except (IntegrationError, RequestConflict):
            draft.publishing = False
            draft.uncertain = writing
            self.repository.save(draft)
            raise
        self.repository.save(draft)
        return draft
