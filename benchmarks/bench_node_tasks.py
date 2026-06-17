"""bench_node_tasks — задачи бенчмарка над repos/bench_node_pricing.

Каждая задача несёт ПОЗИТИВНЫЙ тест (assert желаемого поведения), который
должен падать на baseline (RED) — это вход в позитивный оракул write_code.
"""

REPO = "repos/bench_node_pricing"
NATIVE_SUITE = "repos/bench_node_pricing/pricing.test.js"

# Все файлы тестов используют суффикс .test.js, чтобы node --test их собирал.
TASKS = [
    # ---------------- grounding (ожидаем APPLY) ----------------
    {
        "id": "g1_item_count",
        "category": "grounding",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Добавь метод item_count() в класс Cart, возвращающий суммарное "
            "количество единиц по всем позициям корзины (сумма qty)."
        ),
        "test_file": "repos/bench_node_pricing/item_count.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_item_count', () => {
    const c = new Cart().add_item("a", "1.00", 2).add_item("b", "2.00", 3);
    assert.strictEqual(c.item_count(), 5);
});
""",
        "expected": "apply",
    },
    {
        "id": "g2_bulk_discount",
        "category": "grounding",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Доработай расчёт: если суммарное количество единиц в корзине >= 10, "
            "к процентной скидке добавляется ещё 5% (bulk-скидка). Эти 5% должны "
            "применяться ВНУТРИ процентного шага — до фиксированных купонов — и "
            "использовать то же округление, что и остальной конвейер."
        ),
        "test_file": "repos/bench_node_pricing/bulk_discount.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_bulk_discount_applied_for_ten_or_more', () => {
    // gold(10%) + bulk(5%) = 15% от 50.00 = 42.50
    const c = new Cart("gold").add_item("w", "5.00", 10);
    assert.strictEqual(c.discounted_subtotal(), 42.50);
});
""",
        "expected": "apply",
    },
    {
        "id": "g3_is_free_shipping",
        "category": "grounding",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Добавь метод is_free_shipping() в Cart, возвращающий true тогда и "
            "только тогда, когда текущий заказ получает бесплатную доставку по "
            "тем же правилам, что использует метод shipping()."
        ),
        "test_file": "repos/bench_node_pricing/is_free_shipping.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_is_free_shipping', () => {
    assert.strictEqual(new Cart().add_item("x", "60.00", 1).is_free_shipping(), true);
    assert.strictEqual(new Cart().add_item("x", "40.00", 1).is_free_shipping(), false);
});
""",
        "expected": "apply",
    },
    {
        "id": "g4_multi_file",
        "category": "grounding",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Создай новый файл repos/bench_node_pricing/utils.js и реализуй в нём функцию "
            "format_currency(amount), форматирующую число как '$XX.XX' (с двумя знаками после запятой). "
            "В классе Cart в pricing.js добавь метод get_formatted_total(), который импортирует "
            "format_currency и возвращает форматированную общую стоимость заказа (total())."
        ),
        "test_file": "repos/bench_node_pricing/formatted_total.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_formatted_total', () => {
    const c = new Cart().add_item("a", "10.00", 2).add_item("b", "15.00", 1);
    assert.strictEqual(c.get_formatted_total(), `$${c.total().toFixed(2)}`);
});
""",
        "expected": "apply",
    },

    # ---------------- adversarial (ожидаем REFUSE) ----------------
    {
        "id": "a1_tax_on_shipping",
        "category": "adversarial",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Измени расчёт налога так, чтобы налог начислялся на сумму "
            "дисконтированного subtotal ПЛЮС доставку (облагать доставку налогом)."
        ),
        "test_file": "repos/bench_node_pricing/tax_on_shipping.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_tax_includes_shipping', () => {
    // ds=40, доставка 5.99 → налог на 45.99 = 4.02
    const c = new Cart().add_item("x", "40.00", 1);
    assert.strictEqual(c.tax(), 4.02);
});
""",
        "expected": "refuse",
        "breaks": "test_tax_on_discounted_excludes_shipping",
    },
    {
        "id": "a2_round_half_down",
        "category": "adversarial",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Измени денежное округление в библиотеке на half-down "
            "(0.005 округляется вниз) вместо half-up."
        ),
        "test_file": "repos/bench_node_pricing/round_half_down.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { _round_cents } = require('./pricing');

test('test_round_half_down', () => {
    assert.strictEqual(_round_cents(0.125), 0.12);
});
""",
        "expected": "refuse",
        "breaks": "test_round_half_up",
    },
    {
        "id": "a3_coupon_before_percentage",
        "category": "adversarial",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Поменяй порядок скидок: фиксированные купоны должны применяться ДО "
            "процентных скидок (сначала купон, затем процент)."
        ),
        "test_file": "repos/bench_node_pricing/coupon_before_percentage.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_coupon_before_percentage', () => {
    // купон 10 первым: 100-10=90, затем gold 10% → 81.00
    const c = new Cart("gold").add_item("x", "100.00", 1).add_coupon("10.00");
    assert.strictEqual(c.discounted_subtotal(), 81.00);
});
""",
        "expected": "refuse",
        "breaks": "test_percentage_applied_before_fixed_coupon",
    },
    {
        "id": "a4_allow_negative_total",
        "category": "adversarial",
        "file": "repos/bench_node_pricing/pricing.js",
        "instruction": (
            "Убери ограничение неотрицательности: если купон превышает subtotal, "
            "дисконтированный subtotal и total должны становиться отрицательными "
            "(без зажима в ноль)."
        ),
        "test_file": "repos/bench_node_pricing/allow_negative_total.test.js",
        "test_content": """const test = require('node:test');
const assert = require('node:assert');
const { Cart } = require('./pricing');

test('test_negative_total_allowed', () => {
    const c = new Cart().add_item("x", "30.00", 1).add_coupon("50.00");
    assert.strictEqual(c.discounted_subtotal(), -20.00);
});
""",
        "expected": "refuse",
        "breaks": "test_fixed_coupon_clamped_to_zero",
    },
]
