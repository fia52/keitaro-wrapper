"""Описывает черновики потоков и правила распределения трафика."""

from dataclasses import dataclass, field


@dataclass
class Offer:
    """Хранит долю, фиксацию и архивное состояние оффера."""

    id: int
    name: str
    share: int
    pinned: bool = False
    removed: bool = False


@dataclass
class Draft:
    """Отделяет локальный состав офферов от снимка Keitaro."""

    id: int
    campaign_id: int
    baseline: dict
    offers: list[Offer] = field(default_factory=list)
    revision: int = 0
    dirty: bool = False
    publishing: bool = False
    publishing_until: float = 0.0
    uncertain: bool = False

    @property
    def editable(self) -> bool:
        """Разрешает редактирование только офферных потоков."""
        return bool(self.baseline.get('offers')) or bool(self.offers)


def distribute(offers: list[Offer], held_id: int | None = None) -> None:
    """Делит остаток поровну, сохраняя фиксации и введённую вручную долю."""
    active: list[Offer] = [offer for offer in offers if not offer.removed]
    fixed: list[Offer] = [
        offer for offer in active if offer.pinned or offer.id == held_id
    ]
    free: list[Offer] = [offer for offer in active if offer not in fixed]
    remainder: int = 100 - sum(offer.share for offer in fixed)
    if remainder < 0 or (active and not free and remainder != 0):
        raise ValueError('Закреплённые доли не позволяют получить сумму 100%.')
    if free:
        quotient: int
        extra: int
        quotient, extra = divmod(remainder, len(free))
        for index, offer in enumerate(free):
            offer.share = quotient + int(index < extra)
    for offer in offers:
        if offer.removed:
            offer.share = 0


def signature(stream: dict) -> dict:
    """Убирает служебные timestamps и ID связей из снимка для сравнения."""
    result: dict = {
        key: value
        for key, value in stream.items()
        if key not in {'updated_at', 'created_at', 'offers'}
    }
    result['offers'] = sorted(
        [
            {
                'offer_id': int(item['offer_id']),
                'share': int(item['share']),
                'state': item.get('state', 'active'),
            }
            for item in stream.get('offers', [])
        ],
        key=lambda item: item['offer_id'],
    )
    return result
