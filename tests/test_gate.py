"""Тесты гейта (model-free) — гоняются в CI без Ollama.

Строят дифы к фикстуре repos/bench_pricing и проверяют детерминированный вердикт:
good → merge, регрессия → reject, ссылка на несуществующий символ → reject.
"""
import difflib
from pathlib import Path

from greenlock.gate import verify_patch

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "bench_pricing"


def _diff(transform) -> str:
    orig = (REPO / "pricing.py").read_text(encoding="utf-8").splitlines(keepends=True)
    new = transform(list(orig))
    return "".join(difflib.unified_diff(
        orig, new, fromfile="a/pricing.py", tofile="b/pricing.py"))


def _add_method(lines):
    ins = ["    def item_count(self) -> int:\n",
           "        return sum(it.qty for it in self._items)\n", "\n"]
    for i, l in enumerate(lines):
        if l.startswith("    def total(self)"):
            return lines[:i] + ins + lines[i:]
    return lines


# тест, который РЕАЛЬНО исполняет добавленный метод item_count (даёт покрытие)
_EXTRA_ITEM_COUNT = {
    "test_item_count_gl.py":
        "from bench_pricing.pricing import Cart\n"
        "def test_item_count_gl():\n"
        "    c = Cart()\n"
        "    assert c.item_count() == 0\n"
        "    c.add_item('x', '1.00', 3)\n"
        "    assert c.item_count() == 3\n"
}


def test_good_change_merges():
    """Покрытая правка (новый метод + тест, который его исполняет) → merge."""
    v = verify_patch(str(REPO), _diff(_add_method), extra_tests=_EXTRA_ITEM_COUNT)
    assert v["decision"] == "merge", v["reasons"]


def test_uncovered_change_degraded():
    """Тот же новый метод, но БЕЗ теста на него → честный degraded-reject (WS-1):
    нельзя ручаться за код, который тесты не исполняют."""
    v = verify_patch(str(REPO), _diff(_add_method))
    assert v["decision"] == "reject"
    assert v["confidence"] == "degraded", v["reasons"]


def test_behavior_regression_rejected():
    bad = lambda L: [l.replace(
        "return _round_cents(ds * TAX_RATE)",
        "return _round_cents((ds + self.shipping()) * TAX_RATE)") for l in L]
    v = verify_patch(str(REPO), _diff(bad))
    assert v["decision"] == "reject"
    assert v["regression"] is True


def test_closed_world_violation_rejected():
    cw = lambda L: [l.replace(
        'return _round_cents(sum((it.line_total for it in self._items), Decimal("0")))',
        "return nonexistent_helper(self._items)") for l in L]
    v = verify_patch(str(REPO), _diff(cw))
    assert v["decision"] == "reject"
    assert v["closed_world"]


def test_empty_diff_rejected():
    v = verify_patch(str(REPO), "")
    assert v["decision"] == "reject"


def test_hints_suggest_fixes():
    from greenlock.gate import _hints
    assert any("harden" in h for h in
               _hints({"failing_stage": "coverage", "reasons": [], "decision": "reject"}))
    assert any("trust" in h for h in _hints(
        {"danger": ["x: subprocess.run"], "failing_stage": None,
         "reasons": ["danger … — отказ (не исполнялось)"], "decision": "reject"}))
    assert any("регресс" in h.lower() for h in
               _hints({"regression": True, "reasons": [], "decision": "reject"}))


def test_ws4_new_symbol_resolved_from_patched_index(tmp_path):
    """WS-4: символ, добавленный патчем и использованный через `from a import *`
    (wildcard трекинг импортов не видит), не должен считаться «несуществующим».
    Это требует индекса ПОПАТЧЕННОЙ песочницы, а не репо до патча."""
    a0 = "def old():\n    return 1\n"
    b0 = "from a import *\n\n\ndef run():\n    return old()\n"
    (tmp_path / "a.py").write_text(a0, encoding="utf-8")
    (tmp_path / "b.py").write_text(b0, encoding="utf-8")
    (tmp_path / "test_b.py").write_text(
        "from b import run\ndef test_run():\n    assert run() == 3\n", encoding="utf-8")

    a1 = "def old():\n    return 1\n\n\ndef new_util():\n    return 2\n"
    b1 = "from a import *\n\n\ndef run():\n    return old() + new_util()\n"
    diff = ("".join(difflib.unified_diff(a0.splitlines(keepends=True),
                                         a1.splitlines(keepends=True),
                                         fromfile="a/a.py", tofile="b/a.py"))
            + "".join(difflib.unified_diff(b0.splitlines(keepends=True),
                                           b1.splitlines(keepends=True),
                                           fromfile="a/b.py", tofile="b/b.py")))
    v = verify_patch(str(tmp_path), diff)
    assert v["closed_world"] == [], v["closed_world"]   # new_util не «несуществующий»
    assert v["decision"] == "merge", v["reasons"]
