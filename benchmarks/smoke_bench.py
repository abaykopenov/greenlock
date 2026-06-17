#!/usr/bin/env python3
"""smoke_bench.py — проверка САМОГО раннера без Ollama: подменяем генератор
детерминированной «фейковой моделью» и убеждаемся, что петля
patch→sandbox→closed-world→оракул→регрессия отрабатывает на bench_pricing:
  - верный grounding-патч → APPLIED, родной сет зелёный;
  - состязательный патч → REFUSED (регрессия поймана), файл не тронут.
"""
import json
from pathlib import Path

import greenlock.code_writer as cw
from benchmarks.run_bench import make_args, native_suite_green
from greenlock import groundqa as g

REPO = "repos/bench_pricing"
PRICING = Path(REPO) / "pricing.py"

# Что вернёт «модель» (по текущей задаче).
_FAKE = {"patch": None}


def fake_generate(args, model, system, user):
    return "```json\n" + json.dumps(_FAKE["patch"]) + "\n```", {
        "prompt": 10, "completion": 20, "total": 30}


def run_one(task_desc, patch, test_file, test_content):
    _FAKE["patch"] = patch
    args = make_args("fake", "", REPO)   # без эскалации
    index = g.build_index(Path(REPO))
    success, msg, usage, status = cw.write_code(
        args, index, task_desc, "pricing.py",
        additional_test_file=test_file, additional_test_content=test_content)
    return status, msg


def main():
    cw.generate = fake_generate          # подмена генератора
    orig = PRICING.read_text(encoding="utf-8")
    try:
        # 1) ВЕРНЫЙ grounding-патч (item_count) → ожидаем applied + сет зелёный
        st1, msg1 = run_one(
            "Добавь метод item_count() в Cart (сумма qty).",
            {"mode": "insert_symbol", "file": "pricing.py",
             "after_symbol": "Cart.add_coupon",
             "replacement": "    def item_count(self) -> int:\n"
                            "        return sum(it.qty for it in self._items)\n"},
            "test_task_g1.py",
            'from bench_pricing.pricing import Cart\n'
            'def test_item_count():\n'
            '    assert Cart().add_item("a","1.00",2).add_item("b","2.00",3).item_count() == 5\n')
        green1 = native_suite_green()
        applied_changed = (PRICING.read_text(encoding="utf-8") != orig)
        # вернуть файл перед второй задачей
        PRICING.write_text(orig, encoding="utf-8")
        print(f"[grounding/верный]   статус={st1:9} сет_зелёный={green1} "
              f"файл_применён={applied_changed}  "
              f"=> {'OK' if st1=='applied' and green1 and applied_changed else 'СБОЙ'}")

        # 2) СОСТЯЗАТЕЛЬНЫЙ патч (налог на доставку) → ожидаем refused, файл цел
        st2, msg2 = run_one(
            "Облагай налогом и доставку тоже.",
            {"mode": "replace_symbol", "file": "pricing.py", "symbol": "Cart.tax",
             "replacement": '    def tax(self) -> Decimal:\n'
                            '        ds = self.discounted_subtotal()\n'
                            '        if ds == 0:\n'
                            '            return Decimal("0.00")\n'
                            '        return _round_cents((ds + self.shipping()) * TAX_RATE)\n'},
            "test_task_a1.py",
            'from decimal import Decimal\n'
            'from bench_pricing.pricing import Cart\n'
            'def test_tax_includes_shipping():\n'
            '    assert Cart().add_item("x","40.00",1).tax() == Decimal("4.02")\n')
        green2 = native_suite_green()
        untouched = (PRICING.read_text(encoding="utf-8") == orig)
        print(f"[adversarial/злой]   статус={st2:9} сет_зелёный={green2} "
              f"файл_не_тронут={untouched}  "
              f"=> {'OK' if st2 in ('refused','failed') and untouched and green2 else 'СБОЙ'}")
        print(f"    причина отказа: {msg2[:80]}")
    finally:
        PRICING.write_text(orig, encoding="utf-8")   # гарантированно восстановить


if __name__ == "__main__":
    main()
