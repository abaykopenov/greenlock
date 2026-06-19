"""WS-1 для JS: confidence учитывает покрытие изменённых строк (V8 coverage).

Интеграция требует node (со skip). Парсер V8 проверяется синтетически в test_coverage.py.
"""
import difflib
import shutil
from pathlib import Path

import pytest

from greenlock.gate import verify_patch

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "bench_node_pricing"

_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")


def _diff(transform) -> str:
    base = (REPO / "pricing.js").read_text(encoding="utf-8").splitlines(keepends=True)
    new = transform(list(base))
    return "".join(difflib.unified_diff(base, new,
                                        fromfile="a/pricing.js", tofile="b/pricing.js"))


def _add_uncovered_method(lines):
    ins = ["\n", "  item_count() {\n", "    return this._items.length;\n", "  }\n"]
    for i, l in enumerate(lines):
        if l.strip().startswith("add_coupon(amount)"):
            return lines[:i] + ins + lines[i:]
    return lines


def _refactor_covered(lines):
    out = []
    for l in lines:
        if "return Math.round((Number(amount)" in l:
            out.append("  const cents = Math.round((Number(amount) + Number.EPSILON) * 100);\n")
            out.append("  return cents / 100;\n")
        else:
            out.append(l)
    return out


@_node
def test_node_uncovered_change_degraded():
    """Новый JS-метод, который тесты не вызывают → честный degraded-reject (WS-1)."""
    v = verify_patch(str(REPO), _diff(_add_uncovered_method))
    assert v["decision"] == "reject"
    assert v["confidence"] == "degraded", v["reasons"]


@_node
def test_node_covered_change_merges():
    """Рефактор покрытой тестом строки (поведение то же) → merge."""
    v = verify_patch(str(REPO), _diff(_refactor_covered))
    assert v["decision"] == "merge", v["reasons"]
