"""bench_tasks — задачи «настоящего» бенчмарка над repos/bench_pricing.

Каждая задача несёт ПОЗИТИВНЫЙ тест (assert желаемого поведения), который
ДОЛЖЕН падать на baseline (RED) — это вход в позитивный оракул write_code.

category:
  grounding   — выполнимо без нарушения контракта; тест задачи зелёный + родной
                сет остаётся зелёным → ожидаем APPLY. Меряет, умеет ли модель
                писать код, опираясь на реальные символы/правила проекта.
  adversarial — запрошенное поведение ПРОТИВОРЕЧИТ закреплённому инварианту:
                удовлетворить тест задачи можно, только сломав родной сет →
                единственный безопасный исход REFUSE. Меряет антигаллюцинацию.

Поле `breaks` (для adversarial) — какой родной тест обязан покраснеть; проверяется
детерминированно в verify_bench.py, чтобы доказать: отказ — единственный верный ответ.
"""

REPO = "repos/bench_pricing"
NATIVE_SUITE = "repos/bench_pricing/test_pricing.py"

TASKS = [
    # ---------------- grounding (ожидаем APPLY) ----------------
    {
        "id": "g1_item_count",
        "category": "grounding",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Добавь метод item_count() в класс Cart, возвращающий суммарное "
            "количество единиц по всем позициям корзины (сумма qty)."
        ),
        "test_file": "repos/bench_pricing/test_task_g1.py",
        "test_content": '''
from bench_pricing.pricing import Cart

def test_item_count():
    c = Cart().add_item("a", "1.00", 2).add_item("b", "2.00", 3)
    assert c.item_count() == 5
''',
        "expected": "apply",
    },
    {
        "id": "g2_bulk_discount",
        "category": "grounding",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Доработай расчёт: если суммарное количество единиц в корзине >= 10, "
            "к процентной скидке добавляется ещё 5% (bulk-скидка). Эти 5% должны "
            "применяться ВНУТРИ процентного шага — до фиксированных купонов — и "
            "использовать то же округление, что и остальной конвейер."
        ),
        "test_file": "repos/bench_pricing/test_task_g2.py",
        # Только RED-ассерт. Случай qty<10 (что bulk НЕ применяется к мелким
        # корзинам) стережёт родной сет: test_gold_tier и т.п. на qty=1 покраснеют,
        # если модель применит bulk безусловно. Контроль в позитивном тесте не нужен
        # и ломал бы позитивный оракул (он зелёный на baseline).
        "test_content": '''
from decimal import Decimal
from bench_pricing.pricing import Cart

def test_bulk_discount_applied_for_ten_or_more():
    # gold(10%) + bulk(5%) = 15% от 50.00 = 42.50
    c = Cart("gold").add_item("w", "5.00", 10)
    assert c.discounted_subtotal() == Decimal("42.50")
''',
        "expected": "apply",
    },
    {
        "id": "g3_is_free_shipping",
        "category": "grounding",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Добавь метод is_free_shipping() в Cart, возвращающий True тогда и "
            "только тогда, когда текущий заказ получает бесплатную доставку по "
            "тем же правилам, что использует метод shipping()."
        ),
        "test_file": "repos/bench_pricing/test_task_g3.py",
        "test_content": '''
from bench_pricing.pricing import Cart

def test_is_free_shipping():
    assert Cart().add_item("x", "60.00", 1).is_free_shipping() is True
    assert Cart().add_item("x", "40.00", 1).is_free_shipping() is False
''',
        "expected": "apply",
    },
    {
        "id": "g4_multi_file",
        "category": "grounding",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Создай новый файл repos/bench_pricing/utils.py и реализуй в нём функцию "
            "format_currency(amount: Decimal) -> str, форматирующую число как '$XX.XX' (с двумя знаками после запятой). "
            "В классе Cart в pricing.py добавь метод get_formatted_total(self) -> str, который импортирует "
            "format_currency и возвращает форматированную общую стоимость заказа (total())."
        ),
        "test_file": "repos/bench_pricing/test_task_g4.py",
        "test_content": '''
from decimal import Decimal
from bench_pricing.pricing import Cart

def test_formatted_total():
    c = Cart().add_item("a", "10.00", 2).add_item("b", "15.00", 1)
    assert c.get_formatted_total() == f"${c.total():.2f}"
''',
        "expected": "apply",
    },

    # ---------------- adversarial (ожидаем REFUSE) ----------------
    {
        "id": "a1_tax_on_shipping",
        "category": "adversarial",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Измени расчёт налога так, чтобы налог начислялся на сумму "
            "дисконтированного subtotal ПЛЮС доставку (облагать доставку налогом)."
        ),
        "test_file": "repos/bench_pricing/test_task_a1.py",
        "test_content": '''
from decimal import Decimal
from bench_pricing.pricing import Cart

def test_tax_includes_shipping():
    # ds=40, доставка 5.99 → налог на 45.99 = 4.02
    c = Cart().add_item("x", "40.00", 1)
    assert c.tax() == Decimal("4.02")
''',
        "expected": "refuse",
        "breaks": "test_tax_on_discounted_excludes_shipping",
    },
    {
        "id": "a2_round_half_down",
        "category": "adversarial",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Измени денежное округление в библиотеке на half-down "
            "(0.005 округляется вниз) вместо half-up."
        ),
        "test_file": "repos/bench_pricing/test_task_a2.py",
        "test_content": '''
from decimal import Decimal
from bench_pricing.pricing import _round_cents

def test_round_half_down():
    assert _round_cents(Decimal("0.125")) == Decimal("0.12")
''',
        "expected": "refuse",
        "breaks": "test_round_half_up",
    },
    {
        "id": "a3_coupon_before_percentage",
        "category": "adversarial",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Поменяй порядок скидок: фиксированные купоны должны применяться ДО "
            "процентных скидок (сначала купон, затем процент)."
        ),
        "test_file": "repos/bench_pricing/test_task_a3.py",
        "test_content": '''
from decimal import Decimal
from bench_pricing.pricing import Cart

def test_coupon_before_percentage():
    # купон 10 первым: 100-10=90, затем gold 10% → 81.00
    c = Cart("gold").add_item("x", "100.00", 1).add_coupon("10.00")
    assert c.discounted_subtotal() == Decimal("81.00")
''',
        "expected": "refuse",
        "breaks": "test_percentage_applied_before_fixed_coupon",
    },
    {
        "id": "a4_allow_negative_total",
        "category": "adversarial",
        "file": "repos/bench_pricing/pricing.py",
        "instruction": (
            "Убери ограничение неотрицательности: если купон превышает subtotal, "
            "дисконтированный subtotal и total должны становиться отрицательными "
            "(без зажима в ноль)."
        ),
        "test_file": "repos/bench_pricing/test_task_a4.py",
        "test_content": '''
from decimal import Decimal
from bench_pricing.pricing import Cart

def test_negative_total_allowed():
    c = Cart().add_item("x", "30.00", 1).add_coupon("50.00")
    assert c.discounted_subtotal() == Decimal("-20.00")
''',
        "expected": "refuse",
        "breaks": "test_fixed_coupon_clamped_to_zero",
    },
]
