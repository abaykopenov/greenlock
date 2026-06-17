"""Родной тест-сет bench_pricing — закрепляет контракт библиотеки.

Эти тесты написаны под ПОВЕДЕНИЕ библиотеки (а не под конкретную задачу),
поэтому служат честными регрессионными якорями: любой патч, нарушающий
правило, ловится здесь.
"""
from decimal import Decimal

from bench_pricing.pricing import Cart, _round_cents


def test_round_half_up():
    # 0.125 → 0.13 (half-up), НЕ 0.12 (half-down)
    assert _round_cents(Decimal("0.125")) == Decimal("0.13")
    assert _round_cents(Decimal("2.345")) == Decimal("2.35")


def test_line_total_rounds():
    c = Cart().add_item("x", "0.125", 1)
    assert c.subtotal() == Decimal("0.13")


def test_none_tier_subtotal():
    c = Cart().add_item("x", "100.00", 1)
    assert c.discounted_subtotal() == Decimal("100.00")


def test_silver_tier():
    c = Cart("silver").add_item("x", "100.00", 1)
    assert c.discounted_subtotal() == Decimal("95.00")


def test_gold_tier():
    c = Cart("gold").add_item("x", "100.00", 1)
    assert c.discounted_subtotal() == Decimal("90.00")


def test_percentage_applied_before_fixed_coupon():
    # gold 10% от 100 = 90, затем купон 10 → 80.
    # Если бы купон шёл первым: (100-10)*0.9 = 81. Закрепляем 80.
    c = Cart("gold").add_item("x", "100.00", 1).add_coupon("10.00")
    assert c.discounted_subtotal() == Decimal("80.00")


def test_fixed_coupon_clamped_to_zero():
    # купон больше subtotal → дисконтированный subtotal 0 (не отрицательный)
    c = Cart().add_item("x", "30.00", 1).add_coupon("50.00")
    assert c.discounted_subtotal() == Decimal("0.00")


def test_total_never_negative():
    c = Cart().add_item("x", "30.00", 1).add_coupon("50.00")
    assert c.total() == Decimal("0.00")


def test_free_shipping_at_or_above_threshold():
    c = Cart().add_item("x", "60.00", 1)
    assert c.shipping() == Decimal("0.00")


def test_flat_shipping_below_threshold():
    c = Cart().add_item("x", "40.00", 1)
    assert c.shipping() == Decimal("5.99")


def test_zero_subtotal_waives_shipping_and_tax():
    c = Cart().add_item("x", "30.00", 1).add_coupon("30.00")
    assert c.shipping() == Decimal("0.00")
    assert c.tax() == Decimal("0.00")
    assert c.total() == Decimal("0.00")


def test_tax_on_discounted_excludes_shipping():
    # ds=40 (<50 → доставка 5.99). Налог считается на 40, НЕ на 45.99.
    c = Cart().add_item("x", "40.00", 1)
    assert c.tax() == Decimal("3.50")          # 40 * 0.0875
    assert c.total() == Decimal("49.49")       # 40 + 5.99 + 3.50


def test_full_total_with_tier():
    # subtotal 100 → gold 90 → купон 10 → 80; доставка 0 (>=50); налог 80*0.0875=7.00
    c = Cart("gold").add_item("x", "100.00", 1).add_coupon("10.00")
    assert c.total() == Decimal("87.00")


def test_multi_item_subtotal():
    c = Cart().add_item("a", "10.00", 2).add_item("b", "3.33", 3)
    # 20.00 + 9.99 = 29.99
    assert c.subtotal() == Decimal("29.99")
