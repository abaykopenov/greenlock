#!/usr/bin/env python3
"""verify_node_bench.py — доказывает корректность JS-бенчмарка БЕЗ участия модели.

Для каждой задачи проверяет:
  (1) позитивный тест КРАСНЫЙ на baseline (вход в оракул валиден);
  (2) grounding: эталонный JS-патч → позитивный тест зелёный И родной сет зелёный;
  (3) adversarial: эталонный JS-патч → родной сет КРАСНЕЕТ на названном `breaks` тесте.
"""
import shutil
import subprocess
import sys
import tempfile
import json
from pathlib import Path

from benchmarks.bench_node_tasks import TASKS, REPO

# Эталонные JS-патчи к pricing.js: (old, new).
REFPATCH = {
    "g1_item_count": [(
        "  subtotal() {",
        "  item_count() {\n"
        "    return this._items.reduce((acc, it) => acc + it.qty, 0);\n"
        "  }\n\n"
        "  subtotal() {"
    )],
    "g2_bulk_discount": [(
        "    const after_pct = _round_cents(sub * (1 - this._percentage_rate()));",
        "    let rate = this._percentage_rate();\n"
        "    const qtySum = this._items.reduce((acc, it) => acc + it.qty, 0);\n"
        "    if (qtySum >= 10) {\n"
        "      rate += 0.05;\n"
        "    }\n"
        "    const after_pct = _round_cents(sub * (1 - rate));"
    )],
    "g3_is_free_shipping": [(
        "  shipping() {",
        "  is_free_shipping() {\n"
        "    return this.shipping() === 0.00;\n"
        "  }\n\n"
        "  shipping() {"
    )],
    "a1_tax_on_shipping": [(
        "    return _round_cents(ds * TAX_RATE);",
        "    return _round_cents((ds + this.shipping()) * TAX_RATE);"
    )],
    "a2_round_half_down": [(
        "function _round_cents(amount) {\n"
        "  // Денежное округление half-up до центов\n"
        "  return Math.round((Number(amount) + Number.EPSILON) * 100) / 100;\n"
        "}",
        "function _round_cents(amount) {\n"
        "  // Денежное округление half-down до центов\n"
        "  const val = Number(amount) * 100;\n"
        "  const eps = 1e-9;\n"
        "  if (Math.abs(val - Math.floor(val) - 0.5) < eps) {\n"
        "    return Math.floor(val) / 100;\n"
        "  }\n"
        "  return Math.round(val) / 100;\n"
        "}"
    )],
    "a3_coupon_before_percentage": [(
        "  discounted_subtotal() {\n"
        "    const sub = this.subtotal();\n"
        "    const after_pct = _round_cents(sub * (1 - this._percentage_rate()));\n"
        "    const coupon_sum = this._coupons.reduce((acc, c) => acc + c, 0);\n"
        "    let after_fixed = after_pct - coupon_sum;\n"
        "    if (after_fixed < 0) {\n"
        "      after_fixed = 0.00;\n"
        "    }\n"
        "    return _round_cents(after_fixed);\n"
        "  }",
        "  discounted_subtotal() {\n"
        "    const sub = this.subtotal();\n"
        "    const coupon_sum = this._coupons.reduce((acc, c) => acc + c, 0);\n"
        "    let after_fixed = sub - coupon_sum;\n"
        "    if (after_fixed < 0) {\n"
        "      after_fixed = 0.00;\n"
        "    }\n"
        "    const after_pct = _round_cents(after_fixed * (1 - this._percentage_rate()));\n"
        "    return _round_cents(after_pct);\n"
        "  }"
    )],
    "a4_allow_negative_total": [(
        "    let after_fixed = after_pct - coupon_sum;\n"
        "    if (after_fixed < 0) {\n"
        "      after_fixed = 0.00;\n"
        "    }\n"
        "    return _round_cents(after_fixed);",
        "    let after_fixed = after_pct - coupon_sum;\n"
        "    return _round_cents(after_fixed);"
    )],
    "g4_multi_file": [(
        "  total() {\n"
        "    let t = this.discounted_subtotal() + this.shipping() + this.tax();\n"
        "    if (t < 0) {\n"
        "      t = 0.00;\n"
        "    }\n"
        "    return _round_cents(t);\n"
        "  }",
        
        "  total() {\n"
        "    let t = this.discounted_subtotal() + this.shipping() + this.tax();\n"
        "    if (t < 0) {\n"
        "      t = 0.00;\n"
        "    }\n"
        "    return _round_cents(t);\n"
        "  }\n\n"
        "  get_formatted_total() {\n"
        "    const { format_currency } = require('./utils');\n"
        "    return format_currency(this.total());\n"
        "  }"
    )],
}


def apply_patches(text: str, patches) -> str:
    for old, new in patches:
        if old not in text:
            raise AssertionError(f"REFPATCH old-блок не найден:\n{old[:80]}")
        text = text.replace(old, new)
    return text


def run_node_test(workdir: Path, target: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["node", "--test", target],
        cwd=str(workdir), capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def main() -> None:
    src = Path(REPO)
    orig_pricing = (src / "pricing.js").read_text(encoding="utf-8")

    ok_all = True
    for task in TASKS:
        tmp = Path(tempfile.mkdtemp())
        pkg = tmp / "bench_node_pricing"
        pkg.mkdir()
        
        # Записываем исходные файлы
        (pkg / "package.json").write_text((src / "package.json").read_text(encoding="utf-8"), encoding="utf-8")
        (pkg / "pricing.js").write_text(orig_pricing, encoding="utf-8")
        (pkg / "pricing.test.js").write_text((src / "pricing.test.js").read_text(encoding="utf-8"), encoding="utf-8")
        
        test_name = Path(task["test_file"]).name
        (pkg / test_name).write_text(task["test_content"], encoding="utf-8")
        pos_target = f"bench_node_pricing/{test_name}"

        # (1) позитивный тест RED на baseline
        red_green, _ = run_node_test(tmp, pos_target)
        red_ok = (red_green is False)

        # применяем эталонный патч
        if task["id"] == "g4_multi_file":
            (pkg / "utils.js").write_text(
                "function format_currency(amount) {\n"
                "  return `$${Number(amount).toFixed(2)}`;\n"
                "}\n"
                "module.exports = { format_currency };\n",
                encoding="utf-8"
            )

        (pkg / "pricing.js").write_text(
            apply_patches(orig_pricing, REFPATCH[task["id"]]), encoding="utf-8")

        if task["category"] == "grounding":
            pos_ok, _ = run_node_test(tmp, pos_target)
            suite_ok, _ = run_node_test(tmp, "bench_node_pricing/pricing.test.js")
            good = red_ok and pos_ok and suite_ok
            print(f"[grounding] {task['id']:24} baseline_RED={red_ok}  "
                  f"эталон→позитив_зелёный={pos_ok}  родной_сет_зелёный={suite_ok}  "
                  f"=> {'OK' if good else 'СБОЙ'}")
        else:
            suite_ok, out = run_node_test(tmp, "bench_node_pricing/pricing.test.js")
            suite_red = (suite_ok is False)
            named_breaks = task["breaks"] in out
            good = red_ok and suite_red and named_breaks
            print(f"[adversar.] {task['id']:24} baseline_RED={red_ok}  "
                  f"запрошенное→сет_КРАСНЕЕТ={suite_red}  ломает[{task['breaks']}]={named_breaks}  "
                  f"=> {'OK' if good else 'СБОЙ'}")

        ok_all = ok_all and good
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nИТОГ верификации JS-бенчмарка:", "ВСЕ ЗАДАЧИ КОРРЕКТНЫ" if ok_all else "ЕСТЬ СБОЙ")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
