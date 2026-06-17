"""bench_pricing.pricing — движок ценообразования корзины.

Бизнес-правила НАМЕРЕННО неочевидны и закреплены в test_pricing.py:
их нельзя «угадать» — нужно читать существующий код и тесты. Это и есть
настоящая проверка grounding (а не памяти модели).

Конвейер расчёта (порядок ВАЖЕН):
  1. subtotal      = Σ round_half_up(unit_price * qty)
  2. процентные скидки (лояльность) — ПЕРВЫМИ
  3. фиксированные купоны — ПОСЛЕ процентных; результат не опускается ниже 0
  4. доставка: 0, если дисконтированный subtotal >= порога; иначе фикс. ставка;
     если дисконтированный subtotal == 0 — доставка тоже 0 (пустой по стоимости заказ)
  5. налог: на ДИСКОНТИРОВАННЫЙ subtotal, БЕЗ учёта доставки; при subtotal 0 — налог 0
  6. total = дисконт. subtotal + доставка + налог, не ниже 0
Округление денег — half-up до центов на каждом денежном шаге.
"""
from decimal import Decimal, ROUND_HALF_UP

TAX_RATE = Decimal("0.0875")
FREE_SHIP_THRESHOLD = Decimal("50.00")
SHIP_FLAT = Decimal("5.99")
LOYALTY_RATES = {
    "none": Decimal("0"),
    "silver": Decimal("0.05"),
    "gold": Decimal("0.10"),
}


def _round_cents(amount) -> Decimal:
    """Округление денежной суммы до центов, half-up."""
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class LineItem:
    """Позиция корзины."""

    def __init__(self, name: str, unit_price, qty: int = 1):
        self.name = name
        self.unit_price = Decimal(str(unit_price))
        self.qty = int(qty)

    @property
    def line_total(self) -> Decimal:
        return _round_cents(self.unit_price * self.qty)


class Cart:
    """Корзина с конвейером скидок, доставки и налога."""

    def __init__(self, loyalty: str = "none"):
        self._items: list[LineItem] = []
        self._coupons: list[Decimal] = []
        self.loyalty = loyalty

    def add_item(self, name: str, unit_price, qty: int = 1) -> "Cart":
        self._items.append(LineItem(name, unit_price, qty))
        return self

    def add_coupon(self, amount) -> "Cart":
        """Фиксированный купон на сумму (применяется после процентных скидок)."""
        self._coupons.append(Decimal(str(amount)))
        return self

    def _percentage_rate(self) -> Decimal:
        return LOYALTY_RATES[self.loyalty]

    def subtotal(self) -> Decimal:
        return _round_cents(sum((it.line_total for it in self._items), Decimal("0")))

    def discounted_subtotal(self) -> Decimal:
        """subtotal → процентные скидки → фиксированные купоны (в этом порядке),
        результат не ниже нуля."""
        sub = self.subtotal()
        after_pct = _round_cents(sub * (Decimal("1") - self._percentage_rate()))
        after_fixed = after_pct - sum(self._coupons, Decimal("0"))
        if after_fixed < 0:
            after_fixed = Decimal("0.00")
        return _round_cents(after_fixed)

    def shipping(self) -> Decimal:
        ds = self.discounted_subtotal()
        if ds == 0:
            return Decimal("0.00")
        return Decimal("0.00") if ds >= FREE_SHIP_THRESHOLD else SHIP_FLAT

    def tax(self) -> Decimal:
        ds = self.discounted_subtotal()
        if ds == 0:
            return Decimal("0.00")
        return _round_cents(ds * TAX_RATE)

    def total(self) -> Decimal:
        t = self.discounted_subtotal() + self.shipping() + self.tax()
        if t < 0:
            t = Decimal("0.00")
        return _round_cents(t)
